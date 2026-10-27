"""Conector SQL Server SOMENTE LEITURA.

Só monta e executa SELECT; tudo passa por `app.core.sql_guard.garante_somente_leitura`.
O teste tests/test_conector_somente_leitura.py varre este pacote e falha se surgir comando de escrita.
O usuário da origem pode não ser somente leitura: cada consulta roda em transação com ROLLBACK e timeout.
Nesta entrega não há conexão com origem real.
"""
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.connectors.sqlserver.politicas import TabelaRecusada
from app.core.config import Settings
from app.core.sql_guard import executa_somente_leitura, garante_somente_leitura

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
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

    @classmethod
    def do_ambiente(cls, s: Settings) -> "ConfigSqlServer":
        return cls(s.sqlserver_host, int(s.sqlserver_port), s.sqlserver_database, s.sqlserver_user,
                   s.sqlserver_password, int(s.sqlserver_timeout), int(s.amostra_limite))

    def __repr__(self) -> str:  # nunca expõe host, usuário nem senha (logs, tracebacks)
        return (f"ConfigSqlServer(host=***, port={self.port}, database={self.database!r}, user=***, password=***, "
                f"timeout={self.timeout}, amostra_limite={self.amostra_limite})")

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


class SqlServerConnector:
    def __init__(self, config: ConfigSqlServer, nome: str | None = None):
        self.config = config
        self.nome = nome

    def montar_select(self, tabela: str, colunas: list[str] | None = None, campo_incremental: str | None = None,
                      limite: int | None = None, ordem: list[str] | tuple[str, ...] | None = None) -> str:
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
            sql += f" WHERE {_ident(campo_incremental)} > %s ORDER BY {_ident(campo_incremental)}"
        elif ordem:
            sql += " ORDER BY " + ", ".join(_ident(c) for c in ordem)
        return garante_somente_leitura(sql)

    def _conectar(self):
        import pymssql  # import tardio: o driver só é necessário com origem real
        c = self.config
        # Coolify entrega variáveis como texto: porta e timeout sempre int
        return pymssql.connect(server=c.host, port=int(c.port), user=c.user, password=c.password,
                               database=c.database, login_timeout=int(c.timeout), timeout=int(c.timeout),
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
                                  limite=limite, ordem=ordem)

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
            f"WHERE ({nulos}) ORDER BY {', '.join(_ident(c) for c in cols)}")

    def ler_nulos(self, conn, tabela: str, politica, ordem: tuple[str, ...], limite: int) -> list[dict]:
        return executa_somente_leitura(conn, self.montar_nulos(politica, tabela, ordem, limite), limite=limite)

    def montar_contagem(self, politica, tabela: str) -> str:
        politica.colunas_select(tabela)  # só tabela autorizada, com colunas aprovadas
        return garante_somente_leitura(f"SELECT COUNT(*) FROM {_ident('dbo.' + tabela.split('.')[-1])}")

    def montar_assinatura(self, politica, tabela: str, campo_incremental: str | None) -> str:
        """AG3: COUNT(*) e MAX(campo incremental) — uma linha por tabela, sem ler nenhum registro."""
        cols = politica.colunas_select(tabela)
        t = _ident("dbo." + tabela.split(".")[-1])
        if campo_incremental and campo_incremental in cols:
            return garante_somente_leitura(f"SELECT COUNT(*), MAX({_ident(campo_incremental)}) FROM {t}")
        return garante_somente_leitura(f"SELECT COUNT(*), NULL FROM {t}")

    def assinatura(self, conn, tabela: str, politica, campo_incremental: str | None) -> list:
        total, maximo = executa_somente_leitura(conn, self.montar_assinatura(politica, tabela, campo_incremental),
                                                limite=1, como_dict=False)[0]
        return [int(total or 0), None if maximo is None else str(maximo)]

    def montar_verificacao_chave(self, politica, tabela: str, ordem: tuple[str, ...]) -> tuple[str, str]:
        """Keyset exige chave única e sem NULL: (total + linhas com NULL na chave, combinações distintas da chave)."""
        cols = politica.colunas_select(tabela)
        if not ordem or any(c not in cols for c in ordem):
            raise TabelaRecusada("Chave da carga completa fora das colunas da política.")
        t = _ident("dbo." + tabela.split(".")[-1])
        nulos = " OR ".join(f"{_ident(c)} IS NULL" for c in ordem)
        q1 = f"SELECT COUNT(*), SUM(CASE WHEN {nulos} THEN 1 ELSE 0 END) FROM {t}"
        completa = " AND ".join(f"{_ident(c)} IS NOT NULL" for c in ordem)  # unicidade entre as chaves completas
        q2 = (f"SELECT COUNT(*) FROM (SELECT DISTINCT {', '.join(_ident(c) for c in ordem)} FROM {t} "
              f"WHERE {completa}) AS chaves")
        return garante_somente_leitura(q1), garante_somente_leitura(q2)

    def verificar_chave(self, conn, tabela: str, politica, ordem: tuple[str, ...]) -> dict:
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
                   limite: int, desde: tuple[str, Any] | None = None) -> list[dict]:
        sql, params = self.montar_pagina(politica, tabela, ordem, limite, ultima, desde=desde)
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
