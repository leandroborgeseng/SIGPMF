"""Conector SQL Server SOMENTE LEITURA.

Só monta e executa SELECT; tudo passa por `app.core.sql_guard.garante_somente_leitura`.
O teste tests/test_conector_somente_leitura.py varre este pacote e falha se surgir comando de escrita.
O usuário da origem pode não ser somente leitura: cada consulta roda em transação com ROLLBACK e timeout.
Nesta entrega não há conexão com origem real.
"""
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from app.connectors.sqlserver.politicas import TabelaRecusada
from app.core.config import Settings
from app.core.sql_guard import executa_somente_leitura, garante_somente_leitura

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
# Uma linha por coluna de chave da PK. Sem COUNT e sem DISTINCT: a unicidade/NULL vêm do catálogo.
_CATALOGO_PK = (
    "SELECT c.name AS coluna, ic.key_ordinal AS ordinal, c.is_nullable AS anulavel, "
    "i.is_disabled AS desabilitado, i.is_primary_key AS pk, i.is_unique AS unico, i.type_desc AS tipo "
    "FROM sys.indexes AS i "
    "INNER JOIN sys.tables AS t ON t.object_id = i.object_id "
    "INNER JOIN sys.schemas AS s ON s.schema_id = t.schema_id "
    "INNER JOIN sys.index_columns AS ic ON ic.object_id = i.object_id AND ic.index_id = i.index_id "
    "INNER JOIN sys.columns AS c ON c.object_id = ic.object_id AND c.column_id = ic.column_id "
    "WHERE s.name = %s AND t.name = %s AND i.is_primary_key = 1 AND ic.is_included_column = 0 "
    "AND ic.key_ordinal > 0 ORDER BY ic.key_ordinal"
)


def depois_da_chave(cursor: tuple | None, valores: tuple) -> bool:
    """True se a linha vem depois do cursor na ordem da PK (comparação estrita, sem NULL)."""
    if cursor is None:
        return True
    if len(cursor) != len(valores):
        raise ValueError("Cursor e linha com tamanhos diferentes.")
    for anterior, atual in zip(cursor, valores, strict=True):
        if anterior is None or atual is None:
            raise ValueError("PK alinhada não compara NULL.")
        if atual > anterior:
            return True
        if atual < anterior:
            return False
    return False


def _bit_ligado(v) -> bool:
    return v in (1, True)


def _bit_desligado(v) -> bool:
    return v in (0, False)


def interpreta_catalogo_pk(linhas, ordem: tuple[str, ...]) -> dict:
    """Confere a PK habilitada, única, clustered e NOT NULL, na ordem pedida. Não olha linhas da tabela."""
    base = {"por_catalogo": True, "total": None, "distintas": None, "pk_ok": False, "nulos": None, "motivo": None}
    if not linhas:
        base["motivo"] = ("Não há PK habilitada no catálogo (sys.indexes). "
                          "A carga não executa COUNT nem DISTINCT e não segue com outra ordem.")
        return base
    if len(linhas) != len(ordem):
        nomes = ", ".join(str(r[0]) for r in linhas)
        base["motivo"] = (f"A PK do catálogo tem {len(linhas)} colunas ({nomes}); "
                          f"a paginação exige {len(ordem)} ({', '.join(ordem)}).")
        return base
    try:
        rows = sorted(linhas, key=lambda r: int(r[1]))
        ordinais = [int(r[1]) for r in rows]
    except (TypeError, ValueError):
        base["motivo"] = "O catálogo da PK devolveu ordinal inválido. A ordem antiga não foi reutilizada."
        return base
    if ordinais != list(range(1, len(ordem) + 1)):
        base["motivo"] = "A PK do catálogo não tem ordinais 1..n contínuos. Não foi usada outra ordem."
        return base
    nomes = [str(r[0]) for r in rows]
    if [n.casefold() for n in nomes] != [c.casefold() for c in ordem]:
        base["motivo"] = (f"A PK do catálogo está na ordem {', '.join(nomes)}; "
                          f"a paginação exige {', '.join(ordem)}. A ordem antiga não foi reutilizada.")
        return base
    if any(not _bit_desligado(r[2]) for r in rows):
        ruins = ", ".join(str(r[0]) for r in rows if not _bit_desligado(r[2]))
        base["motivo"] = f"Coluna da PK aceita NULL ({ruins}). Nulos não são contados com COUNT."
        return base
    if any(not _bit_desligado(r[3]) for r in rows):
        base["motivo"] = "A PK está desabilitada no catálogo. A unicidade não foi presumida."
        return base
    if any(not _bit_ligado(r[4]) or not _bit_ligado(r[5]) for r in rows):
        base["motivo"] = "O índice do catálogo não está marcado como PK única."
        return base
    tipos = {str(r[6]).upper() for r in rows}
    if tipos != {"CLUSTERED"}:
        base["motivo"] = (f"A PK do catálogo não é clustered ({', '.join(sorted(tipos))}). "
                          "O keyset alinhado não foi executado.")
        return base
    base.update(pk_ok=True, nulos=0, motivo=None, pk=list(ordem))
    return base


# varchar(n) só entra se o texto for exatamente esse formato. O número não vira SQL solto.
_VARCHAR_PK = re.compile(r"varchar\(([1-9]\d{0,3})\)$")


def _tipos_keyset(politica, tabela: str) -> dict[str, str]:
    from app.ingestion.mapeamentos import keyset_de
    spec = keyset_de(getattr(politica, "sistema", ""), tabela) or {}
    tipos = spec.get("tipos") or {}
    if spec.get("prova") == "catalogo_pk":
        faltam = [c for c in spec.get("ordem") or () if c not in tipos]
        if faltam:
            raise TabelaRecusada(f"Paginação alinhada sem tipo de parâmetro para: {', '.join(faltam)}")
    return tipos


def _cmp_pk(coluna: str, op: str, tipos: dict[str, str]) -> str:
    """Compara a coluna com o parâmetro já convertido para o tipo dela. A coluna não é convertida.

    Data usa CONVERT estilo 126 (ISO 8601). CAST(datetime) e um literal sem estilo seguem o DATEFORMAT
    da sessão: com login dmy, '2024-01-02' pode virar 1º de fevereiro.
    """
    tipo = tipos.get(coluna)
    if not tipo:
        return f"{_ident(coluna)} {op} %s"
    if tipo == "datetime":
        return f"{_ident(coluna)} {op} CONVERT(datetime, %s, 126)"
    if tipo == "int":
        return f"{_ident(coluna)} {op} CAST(%s AS int)"
    varchar = _VARCHAR_PK.fullmatch(tipo)
    if varchar:
        return f"{_ident(coluna)} {op} CAST(%s AS varchar({varchar.group(1)}))"
    raise TabelaRecusada(f"Tipo de parâmetro não permitido para {coluna}.")


def _iso_126(valor) -> str:
    """Texto estilo 126: `AAAA-MM-DDTHH:MM:SS.mmm`. Três dígitos, sem espaço e sem prefixo N."""
    if isinstance(valor, datetime):
        dt = valor
    elif isinstance(valor, date):
        dt = datetime(valor.year, valor.month, valor.day)
    else:
        dt = datetime.fromisoformat(str(valor).strip().replace("Z", "+00:00"))
    if dt.tzinfo is not None:
        dt = dt.replace(tzinfo=None)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}"


def _valor_pk(coluna: str, valor, tipos: dict[str, str]):
    """Valor enviado com o CONVERT/CAST. Data é texto ISO; int fica int; varchar fica str, sem N."""
    tipo = tipos.get(coluna)
    if tipo == "datetime":
        return _iso_126(valor)
    if tipo == "int":
        if isinstance(valor, bool):
            raise ValueError(f"Parâmetro int inválido para {coluna}.")
        return int(valor)
    if _VARCHAR_PK.fullmatch(tipo or ""):
        return str(valor)
    return valor


def _ident(nome: str) -> str:
    partes = nome.split(".")
    if not 1 <= len(partes) <= 2 or not all(_IDENT.match(p) for p in partes):
        raise ValueError(f"Identificador inválido: {nome!r}")
    return ".".join(f"[{p}]" for p in partes)


@dataclass(repr=False)
class ConfigSqlServer:
    host: str | None
    port: int
    database: str | None
    user: str | None
    password: str | None
    timeout: int
    amostra_limite: int
    consulta_timeout: int = 120  # por consulta (o `timeout` acima vale para o login)

    @classmethod
    def do_ambiente(cls, s: Settings) -> "ConfigSqlServer":
        return cls(s.sqlserver_host, int(s.sqlserver_port), s.sqlserver_database, s.sqlserver_user,
                   s.sqlserver_password, int(s.sqlserver_timeout), int(s.amostra_limite),
                   int(s.sqlserver_consulta_timeout))

    def __repr__(self) -> str:  # nunca expõe host, usuário nem senha (logs, tracebacks)
        return (f"ConfigSqlServer(host=***, port={self.port}, database={self.database!r}, user=***, password=***, "
                f"timeout={self.timeout}, consulta_timeout={self.consulta_timeout}, amostra_limite={self.amostra_limite})")

    __str__ = __repr__

    @classmethod
    def para_conector(cls, s: Settings, conector: str) -> tuple["ConfigSqlServer", bool]:
        """Banco específico do conector (SQLSERVER_DATABASE_<CONECTOR>); sem ele, usa SQLSERVER_DATABASE.
        Retorna (config, usou_fallback)."""
        especifico = getattr(s, f"sqlserver_database_{conector.lower()}", None)
        base = cls.do_ambiente(s)
        if especifico:
            base.database = especifico
            return base, False
        return base, True

    def resumo_publico(self) -> dict[str, Any]:
        """Sem senha nem usuário."""
        return {"host": self.host, "port": self.port, "database": self.database,
                "configurado": bool(self.host and self.database and self.user),
                "amostra_limite": self.amostra_limite}


def _recorte(politica, tabela: str) -> str:
    """TR1: recorte fixo da política (ex.: exercícios recentes) ou "" — só coluna da política e inteiro."""
    f = getattr(politica, "filtro_fixo", None)
    return f(tabela) if f else ""


def _onde(politica, tabela: str) -> str:
    r = _recorte(politica, tabela)
    return f" WHERE {r}" if r else ""


def _e(politica, tabela: str) -> str:
    r = _recorte(politica, tabela)
    return f" AND {r}" if r else ""


class SqlServerConnector:
    def __init__(self, config: ConfigSqlServer, nome: str | None = None):
        self.config = config
        self.nome = nome

    def montar_select(self, tabela: str, colunas: list[str] | None = None, campo_incremental: str | None = None,
                      limite: int | None = None, ordem: list[str] | tuple[str, ...] | None = None,
                      recorte: str = "") -> str:
        maximo = int(self.config.amostra_limite)
        n = maximo if limite is None else limite
        if isinstance(n, bool) or not isinstance(n, int) or n < 1:
            raise ValueError(f"Limite inválido: {limite!r} (mínimo 1).")
        n = min(n, maximo)  # acima do máximo configurado: trunca no máximo
        if not colunas:
            raise ValueError(f"Lista de colunas obrigatória para {tabela!r}: SELECT * não é permitido.")
        cols = ", ".join(_ident(c) for c in colunas)
        sql = f"SELECT TOP ({int(n)}) {cols} FROM {_ident(tabela)}"
        if campo_incremental:
            sql += f" WHERE {_ident(campo_incremental)} > %s" + (f" AND {recorte}" if recorte else "")
            sql += f" ORDER BY {_ident(campo_incremental)}"
        else:
            if recorte:
                sql += f" WHERE {recorte}"
            if ordem:
                sql += " ORDER BY " + ", ".join(_ident(c) for c in ordem)
        return garante_somente_leitura(sql)

    def _conectar(self):
        import pymssql  # import tardio: o driver só é necessário com origem real

        from app.connectors.sqlserver.guardas import conecta_guardado
        c = self.config
        # D1 (QA #53): SEMPRE pela guarda. Vazio, None e só espaços são recusados (BaseRecusada) sem chamar o driver;
        # a conexão usa o nome canônico da lista. Coolify entrega variáveis como texto: porta e timeout sempre int.
        return conecta_guardado(pymssql.connect, c.database, server=c.host, port=int(c.port), user=c.user,
                                password=c.password, login_timeout=int(c.timeout), timeout=int(c.consulta_timeout),
                                autocommit=False)  # transação explícita: sempre ROLLBACK (sql_guard)

    def testar_conexao(self) -> dict[str, Any]:
        if not self.config.resumo_publico()["configurado"]:
            return {"ok": False, "erro": f"Falha ao conectar em {self.nome or 'a origem'}: origem não configurada no ambiente."}
        try:
            conn = self._conectar()
            try:
                executa_somente_leitura(conn, "SELECT 1", limite=1, como_dict=False)
            finally:
                conn.close()
            return {"ok": True, "erro": None}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "erro": self._erro_limpo(exc)}

    def verificar_estrutura(self, politica, limite: int = 20000) -> dict[str, Any]:
        """Compara a política (tabelas + colunas) com INFORMATION_SCHEMA (schema dbo) pelo guard.
        Só nomes de objetos voltam; nenhum dado de linha. Comparação sem distinção de caixa (collation CI)."""
        tabelas = list(politica.colunas)
        for t in tabelas:
            _ident(t)  # nomes da política já são identificadores válidos
        conn = self._conectar()
        try:
            existentes = executa_somente_leitura(
                conn, f"SELECT TOP ({int(limite)}) TABLE_NAME FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA = %s",
                ("dbo",), limite=limite, como_dict=False)
            marc = ", ".join(["%s"] * len(tabelas))
            cols = executa_somente_leitura(
                conn, f"SELECT TOP ({int(limite)}) TABLE_NAME, COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
                      f"WHERE TABLE_SCHEMA = %s AND TABLE_NAME IN ({marc})",
                ("dbo", *tabelas), limite=limite, como_dict=False) if tabelas else []
        finally:
            conn.close()
        tem_tab = {str(r[0]).lower() for r in existentes}
        por_tab: dict[str, set[str]] = {}
        for t, c in cols:
            por_tab.setdefault(str(t).lower(), set()).add(str(c).lower())
        faltando_tabelas = [t for t in tabelas if t.lower() not in tem_tab]
        faltando_colunas = {t: [c for c in politica.colunas[t] if c.lower() not in por_tab.get(t.lower(), set())]
                            for t in tabelas if t.lower() in tem_tab}
        faltando_colunas = {t: v for t, v in faltando_colunas.items() if v}
        return {"ok": not faltando_tabelas and not faltando_colunas, "tabelas_verificadas": len(tabelas),
                "tabelas_faltando": faltando_tabelas, "colunas_faltando": faltando_colunas}

    def diagnostico(self) -> dict[str, Any]:
        """SELECT 1 + DB_NAME() pelo guard (ROLLBACK, timeout). Devolve só o nome do banco conectado."""
        from app.services.diagnostico import categoria, motivo
        try:
            conn = self._conectar()
            try:
                linha = executa_somente_leitura(conn, "SELECT 1 AS ok, DB_NAME() AS banco", limite=1, como_dict=False)
            finally:
                conn.close()
            banco = linha[0][1] if linha and len(linha[0]) > 1 else None
            return {"ok": True, "banco": banco, "erro": None}
        except Exception as exc:  # noqa: BLE001
            c = categoria(exc)
            return {"ok": False, "banco": None, "erro": {"classe": c["classe"], "numero": c["numero"],
                                                         "motivo": motivo(c["classe"], c["numero"], None, c["objeto"])}}

    def _erro_limpo(self, exc: Exception) -> str:
        """Mensagem clara SEM senha, usuário, host nem string de conexão."""
        nome = self.nome or "a origem"
        if isinstance(exc, (TimeoutError, OSError)) or "timeout" in type(exc).__name__.lower():
            return f"Falha ao conectar em {nome}: servidor não respondeu. Verifique a configuração do ambiente."
        return f"Falha ao conectar em {nome}: servidor não respondeu ou recusou a conexão. Verifique a configuração do ambiente."

    def montar_select_politica(self, politica, tabela: str, limite: int | None = None,
                               campo_incremental: str | None = None) -> str:
        """SELECT só com as colunas permitidas pela política do sistema (recusa tabela fora da lista)."""
        cols = list(politica.colunas_select(tabela))
        if campo_incremental and campo_incremental not in cols:
            raise ValueError(f"Campo incremental {campo_incremental!r} fora das colunas da política.")
        ordem = politica.ordem_amostra(tabela) if hasattr(politica, "ordem_amostra") else ()
        return self.montar_select(f"dbo.{tabela.split('.')[-1]}", cols, campo_incremental=campo_incremental,
                                  limite=limite, ordem=ordem, recorte=_recorte(politica, tabela))

    @staticmethod
    def filtro_keyset(ordem: tuple[str, ...], ultima: tuple | None) -> tuple[str, tuple]:
        """Keyset lexicográfico: (a > :a) OR (a = :a AND b > :b) OR ... — sempre parametrizado.
        NULL ordena primeiro no SQL Server: depois de um NULL, "maior" é IS NOT NULL e "igual" é IS NULL."""
        if ultima is None:
            return "", ()
        if len(ultima) != len(ordem):
            raise ValueError("Checkpoint com número de colunas diferente da chave.")
        termos, params = [], []
        for i, col in enumerate(ordem):
            partes = []
            for c, v in zip(ordem[:i], ultima[:i], strict=True):
                if v is None:
                    partes.append(f"{_ident(c)} IS NULL")
                else:
                    partes.append(f"{_ident(c)} = %s")
                    params.append(v)
            v = ultima[i]
            if v is None:
                partes.append(f"{_ident(col)} IS NOT NULL")
            else:
                partes.append(f"{_ident(col)} > %s")
                params.append(v)
            termos.append("(" + " AND ".join(partes) + ")")
        return " WHERE (" + " OR ".join(termos) + ")", tuple(params)

    def montar_pagina_alinhada(self, politica, tabela: str, ordem: tuple[str, ...], limite: int,
                               ultima: tuple | None = None, desde: tuple[str, Any] | None = None) -> tuple[str, tuple]:
        """Página cuja ordem é a PK clustered.

        Sem cursor: TOP + ORDER BY na ordem da PK (scan ordenado limitado; sem Sort e sem IS NOT NULL).
        Com cursor: um ramo de seek por coluna (igualdade no prefixo e desigualdade na coluna seguinte).
        Em T-SQL, ORDER BY só vale no fim do conjunto ou dentro de subconsulta/CTE que tenha TOP ou OFFSET.
        Cada ramo é `SELECT colunas FROM (SELECT TOP (n) ... ORDER BY <sufixo>) AS [ramoN]`. O UNION ALL
        não tem ORDER BY. O SELECT externo limita e ordena no máximo 5 × o lote.
        A janela incremental só entra na primeira página, e só se o campo for a primeira coluna da PK.
        """
        cols = list(politica.colunas_select(tabela))
        fora = [c for c in ordem if c not in cols]
        if not ordem or fora:
            raise TabelaRecusada(f"Ordenação da carga completa fora das colunas da política: {fora or 'vazia'}")
        if isinstance(limite, bool) or not isinstance(limite, int) or limite < 1:
            raise ValueError(f"Lote inválido: {limite!r}")
        if ultima is not None and len(ultima) != len(ordem):
            raise ValueError("Checkpoint com número de colunas diferente da chave.")
        if ultima is not None and any(v is None for v in ultima):
            raise ValueError("Cursor da PK alinhada não aceita NULL.")
        lista = ", ".join(_ident(c) for c in cols)
        origem = _ident("dbo." + tabela.split(".")[-1])
        ordem_sql = ", ".join(_ident(c) for c in ordem)
        recorte = _recorte(politica, tabela)
        tipos = _tipos_keyset(politica, tabela)
        if ultima is None:
            where, params = [], []
            if desde is not None:
                campo, valor = desde
                if campo not in cols:
                    raise TabelaRecusada("Campo incremental fora das colunas da política.")
                if campo != ordem[0]:
                    raise TabelaRecusada(
                        "A janela incremental da paginação alinhada à PK tem de ser a primeira coluna da chave.")
                where.append(_cmp_pk(campo, ">=", tipos))
                params.append(_valor_pk(campo, valor, tipos))
            if recorte:
                where.append(recorte)
            clausula = (" WHERE " + " AND ".join(where)) if where else ""
            sql = f"SELECT TOP ({int(limite)}) {lista} FROM {origem}{clausula} ORDER BY {ordem_sql}"
            return garante_somente_leitura(sql), tuple(params)
        ramos, params = [], []
        for i in range(len(ordem) - 1, -1, -1):
            partes = []
            for c, v in zip(ordem[:i], ultima[:i], strict=True):
                partes.append(_cmp_pk(c, "=", tipos))
                params.append(_valor_pk(c, v, tipos))
            partes.append(_cmp_pk(ordem[i], ">", tipos))
            params.append(_valor_pk(ordem[i], ultima[i], tipos))
            if recorte:
                partes.append(recorte)
            ramo_ordem = ", ".join(_ident(c) for c in ordem[i:])
            interno = (f"SELECT TOP ({int(limite)}) {lista} FROM {origem} WHERE {' AND '.join(partes)} "
                       f"ORDER BY {ramo_ordem}")
            ramos.append(f"SELECT {lista} FROM ({interno}) AS [ramo{len(ramos)}]")
        sql = (f"SELECT TOP ({int(limite)}) {lista} FROM (" + " UNION ALL ".join(ramos)
               + f") AS [pagina] ORDER BY {ordem_sql}")
        return garante_somente_leitura(sql), tuple(params)

    def montar_amostra_unicidade(self, politica, tabela: str, ordem: tuple[str, ...], limite: int) -> str:
        """SELECT de amostra: COUNT(*) , chaves distintas da PK e chaves distintas do id antigo de 4 colunas.

        Um único TOP (n) na CTE. SQL Server não aceita COUNT(DISTINCT de várias colunas; cada contagem
        distinta é COUNT(*) sobre SELECT DISTINCT. Não grava na origem e não entra na carga.
        """
        from app.ingestion.mapeamentos import MAPEAMENTOS
        nome = tabela.split(".")[-1]
        cols = list(politica.colunas_select(nome))
        if not ordem or any(c not in cols for c in ordem) or len(ordem) != len(set(ordem)):
            raise TabelaRecusada("Amostra de unicidade fora das colunas da política.")
        if isinstance(limite, bool) or not isinstance(limite, int) or not 1 <= limite <= 10000:
            raise ValueError(f"Amostra inválida: {limite!r}")
        chave_id = (MAPEAMENTOS.get((getattr(politica, "sistema", ""), nome)) or {}).get("chave")
        if not isinstance(chave_id, tuple) or set(chave_id) != set(ordem) or "Data_Movimentacao" not in chave_id:
            raise TabelaRecusada("Amostra de unicidade exige a PK inteira, com Data_Movimentacao.")
        curta = tuple(c for c in chave_id if c != "Data_Movimentacao")
        pk = ", ".join(_ident(c) for c in ordem)
        antiga = ", ".join(_ident(c) for c in curta)
        origem = _ident("dbo." + nome)
        sql = (
            f"WITH [amostra] AS (SELECT TOP ({int(limite)}) {pk} FROM {origem} ORDER BY {pk}) "
            f"SELECT (SELECT COUNT(*) FROM [amostra]) AS [total], "
            f"(SELECT COUNT(*) FROM (SELECT DISTINCT {pk} FROM [amostra]) AS [distintas]) AS [distintas], "
            f"(SELECT COUNT(*) FROM (SELECT DISTINCT {antiga} FROM [amostra]) AS [legadas]) AS [legadas]"
        )
        return garante_somente_leitura(sql)

    def montar_pagina(self, politica, tabela: str, ordem: tuple[str, ...], limite: int,
                      ultima: tuple | None = None, desde: tuple[str, Any] | None = None) -> tuple[str, tuple]:
        """Página da carga completa por keyset: colunas da política, WHERE chave > última, ORDER BY chave, TOP (lote).
        IN2: com `desde=(campo, valor)`, só as linhas com campo >= valor (carga incremental), parametrizado."""
        cols = list(politica.colunas_select(tabela))
        fora = [c for c in ordem if c not in cols]
        if not ordem or fora:
            raise TabelaRecusada(f"Ordenação da carga completa fora das colunas da política: {fora or 'vazia'}")
        if isinstance(limite, bool) or not isinstance(limite, int) or limite < 1:
            raise ValueError(f"Lote inválido: {limite!r}")
        where, params = self.filtro_keyset(ordem, ultima)
        completa = "(" + " AND ".join(f"{_ident(c)} IS NOT NULL" for c in ordem) + ")"  # CC9: só chave completa
        where = f"{where} AND {completa}" if where else f" WHERE {completa}"
        if _recorte(politica, tabela):
            where = f"{where} AND {_recorte(politica, tabela)}"
        if desde is not None:
            campo, valor = desde
            if campo not in cols:
                raise TabelaRecusada("Campo incremental fora das colunas da política.")
            where = f"{where} AND ({_ident(campo)} >= %s)"
            params = (*params, valor)
        sql = (f"SELECT TOP ({int(limite)}) {', '.join(_ident(c) for c in cols)} "
               f"FROM {_ident('dbo.' + tabela.split('.')[-1])}{where} ORDER BY {', '.join(_ident(c) for c in ordem)}")
        return garante_somente_leitura(sql), params

    def montar_nulos(self, politica, tabela: str, ordem: tuple[str, ...], limite: int) -> str:
        """CC9: SELECT separado e limitado das linhas com algum campo da chave nulo (vão para a quarentena)."""
        cols = list(politica.colunas_select(tabela))
        if not ordem or any(c not in cols for c in ordem):
            raise TabelaRecusada("Chave da carga completa fora das colunas da política.")
        if isinstance(limite, bool) or not isinstance(limite, int) or limite < 1:
            raise ValueError(f"Limite inválido: {limite!r}")
        nulos = " OR ".join(f"{_ident(c)} IS NULL" for c in ordem)
        return garante_somente_leitura(
            f"SELECT TOP ({int(limite)}) {', '.join(_ident(c) for c in cols)} FROM {_ident('dbo.' + tabela.split('.')[-1])} "
            f"WHERE ({nulos}){_e(politica, tabela)} ORDER BY {', '.join(_ident(c) for c in cols)}")

    def ler_nulos(self, conn, tabela: str, politica, ordem: tuple[str, ...], limite: int) -> list[dict]:
        return executa_somente_leitura(conn, self.montar_nulos(politica, tabela, ordem, limite), limite=limite)

    def _sem_varredura(self, politica, tabela: str) -> None:
        from app.ingestion.mapeamentos import keyset_de
        spec = keyset_de(getattr(politica, "sistema", ""), tabela)
        if spec and spec.get("prova") == "catalogo_pk":
            raise TabelaRecusada(
                f"{tabela.split('.')[-1]}: COUNT e DISTINCT não são emitidos; a chave é a PK do catálogo.",
                "contagem_proibida")

    def montar_catalogo_pk(self, tabela: str) -> str:
        _ident(tabela.split(".")[-1])
        return garante_somente_leitura(_CATALOGO_PK)

    def montar_contagem(self, politica, tabela: str) -> str:
        self._sem_varredura(politica, tabela)
        politica.colunas_select(tabela)  # só tabela autorizada, com colunas aprovadas
        return garante_somente_leitura(f"SELECT COUNT(*) FROM {_ident('dbo.' + tabela.split('.')[-1])}{_onde(politica, tabela)}")

    def montar_assinatura(self, politica, tabela: str, campo_incremental: str | None) -> str:
        """AG3: COUNT(*) e MAX(campo incremental) — uma linha por tabela, sem ler nenhum registro."""
        self._sem_varredura(politica, tabela)
        cols = politica.colunas_select(tabela)
        t = _ident("dbo." + tabela.split(".")[-1])
        if campo_incremental and campo_incremental in cols:
            return garante_somente_leitura(f"SELECT COUNT(*), MAX({_ident(campo_incremental)}) FROM {t}{_onde(politica, tabela)}")
        return garante_somente_leitura(f"SELECT COUNT(*), NULL FROM {t}{_onde(politica, tabela)}")

    def assinatura(self, conn, tabela: str, politica, campo_incremental: str | None) -> list:
        from app.ingestion.mapeamentos import keyset_de
        spec = keyset_de(politica.sistema, tabela)
        if spec and spec.get("prova") == "catalogo_pk":
            # Sem COUNT: a agenda não trata a assinatura como estável e não pula a carga.
            return [None, "indisponivel"]
        total, maximo = executa_somente_leitura(conn, self.montar_assinatura(politica, tabela, campo_incremental),
                                                limite=1, como_dict=False)[0]
        return [int(total or 0), None if maximo is None else str(maximo)]

    def montar_verificacao_chave(self, politica, tabela: str, ordem: tuple[str, ...]) -> tuple[str, str]:
        """Keyset exige chave única e sem NULL: (total + linhas com NULL na chave, combinações distintas da chave)."""
        self._sem_varredura(politica, tabela)
        cols = politica.colunas_select(tabela)
        if not ordem or any(c not in cols for c in ordem):
            raise TabelaRecusada("Chave da carga completa fora das colunas da política.")
        t = _ident("dbo." + tabela.split(".")[-1])
        nulos = " OR ".join(f"{_ident(c)} IS NULL" for c in ordem)
        q1 = f"SELECT COUNT(*), SUM(CASE WHEN {nulos} THEN 1 ELSE 0 END) FROM {t}{_onde(politica, tabela)}"
        completa = " AND ".join(f"{_ident(c)} IS NOT NULL" for c in ordem)  # unicidade entre as chaves completas
        q2 = (f"SELECT COUNT(*) FROM (SELECT DISTINCT {', '.join(_ident(c) for c in ordem)} FROM {t} "
              f"WHERE {completa}{_e(politica, tabela)}) AS chaves")
        return garante_somente_leitura(q1), garante_somente_leitura(q2)

    def verificar_chave(self, conn, tabela: str, politica, ordem: tuple[str, ...]) -> dict:
        from app.ingestion.mapeamentos import keyset_de
        spec = keyset_de(politica.sistema, tabela)
        if spec and spec.get("prova") == "catalogo_pk":
            if tuple(ordem) != tuple(spec["ordem"]):
                return {"por_catalogo": True, "pk_ok": False, "total": None, "nulos": None, "distintas": None,
                        "motivo": "A ordem pedida não é a PK declarada para o catálogo. COUNT não foi executado."}
            nome = tabela.split(".")[-1]
            linhas = executa_somente_leitura(conn, self.montar_catalogo_pk(nome), ("dbo", nome),
                                             limite=32, como_dict=False)
            if len(linhas) >= 32:
                return {"por_catalogo": True, "pk_ok": False, "total": None, "nulos": None, "distintas": None,
                        "motivo": "O catálogo devolveu colunas demais para a PK. COUNT não foi executado."}
            return interpreta_catalogo_pk(linhas, tuple(spec["ordem"]))
        q1, q2 = self.montar_verificacao_chave(politica, tabela, ordem)
        total, nulos = executa_somente_leitura(conn, q1, limite=1, como_dict=False)[0]
        distintas = executa_somente_leitura(conn, q2, limite=1, como_dict=False)[0][0]
        return {"total": int(total or 0), "nulos": int(nulos or 0), "distintas": int(distintas or 0)}

    def abrir(self):
        """Conexão única da carga completa (autocommit desligado; o guard faz ROLLBACK após cada consulta)."""
        return self._conectar()

    def contar(self, conn, tabela: str, politica) -> int:
        r = executa_somente_leitura(conn, self.montar_contagem(politica, tabela), limite=1, como_dict=False)
        return int(r[0][0]) if r else 0

    def ler_pagina(self, conn, tabela: str, politica, ordem: tuple[str, ...], ultima: tuple | None,
                   limite: int, desde: tuple[str, Any] | None = None, alinhada: bool = False) -> list[dict]:
        montar = self.montar_pagina_alinhada if alinhada else self.montar_pagina
        sql, params = montar(politica, tabela, ordem, limite, ultima, desde=desde)
        return executa_somente_leitura(conn, sql, params or None, limite=limite)

    def ler_amostra(self, tabela: str, politica, campo_incremental: str | None = None,
                    desde: datetime | None = None) -> list[dict[str, Any]]:
        """Lê no máximo AMOSTRA_LIMITE linhas, sempre com a política do sistema (lista de colunas explícita)."""
        if politica is None:
            raise TabelaRecusada(f"Leitura de {tabela!r} exige a política do sistema.")
        sql = self.montar_select_politica(politica, tabela, campo_incremental=campo_incremental if desde else None)
        conn = self._conectar()
        try:
            return executa_somente_leitura(conn, sql, (desde,) if desde else None, limite=self.config.amostra_limite)
        finally:
            conn.close()
