"""PR-02: base de CEP dos Correios (DNE) e resolução de endereço por CEP (E1-E4).

Fonte (esquema confirmado pelo DBA): `Correios.dbo.LOG_LOGRADOURO_SP` (PK Id_Logradouro) com
`LOG_BAIRRO` (Id_Bairro_Inicial -> Nome_Bairro) e `LOG_LOCALIDADE` (Nome_Localidade, Codigo_IBGE). Carga de todo SP.
- E1: a tabela entra primeiro na bronze (`bronze.registros`, sistema CORREIOS, chave Id_Logradouro); a reconciliação
  compara a contagem da origem com a da bronze, e cada carga registra no log só agregados (total SP e total do
  município de `DNE_IBGE_CONTAGEM`, Franca = 3516200).
- E2: o logradouro de um endereço vem do DNE pelo CEP. O `Codigo_Logradouro` da PMF nunca é usado como chave (D6).
- E3: CEP ausente, lixo ou fora do DNE -> mantém a origem crua com o motivo `cep_nao_encontrado`, sem descartar.
- E4: um DNE novo atualiza `ref.dne_logradouro` por upsert no CEP, sem duplicar. CEP repetido na origem: vence o
  menor Id_Logradouro (determinístico); `colisoes_cep` (CEPs em 2+ linhas) e `duplicados` (linhas descartadas)
  vão no retorno e no log.
Só SELECT na origem. Nenhum valor de entrada vai para log ou mensagem de erro.
"""
import logging
import os
import re
from typing import Any

from sqlalchemy import CHAR, Column, DateTime, Integer, MetaData, String, Table, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.bronze.ingestao import hash_registro, ingere
from app.core.sql_guard import garante_somente_leitura
from app.models.camadas import RegistroBronze

log = logging.getLogger(__name__)

SISTEMA = "CORREIOS"
TABELA = "LOG_LOGRADOURO_SP"
TABELAS_PERMITIDAS = ("LOG_LOGRADOURO_SP", "LOG_BAIRRO", "LOG_LOCALIDADE")
IBGE_FRANCA = 3516200
MOTIVO_CEP_NAO_ENCONTRADO = "cep_nao_encontrado"
_NAO_DIGITO = re.compile(r"\D")

_FROM = (" FROM [dbo].[LOG_LOGRADOURO_SP] AS [l] WITH (NOLOCK)"
         " LEFT JOIN [dbo].[LOG_BAIRRO] AS [b] WITH (NOLOCK) ON [b].[Id_Bairro] = [l].[Id_Bairro_Inicial]"
         " LEFT JOIN [dbo].[LOG_LOCALIDADE] AS [c] WITH (NOLOCK) ON [c].[Id_Localidade] = [l].[Id_Localidade]")
SQL_LOGRADOUROS = garante_somente_leitura(
    "SELECT [l].[Id_Logradouro], [l].[Nome_Logradouro], [l].[Nome_Abreviado_Logradouro], [l].[Tipo_Logradouro],"
    " [l].[Utiliza_Tipo_Logradouro], [l].[UF], [l].[Cep], [l].[Complemento], [b].[Nome_Bairro],"
    " [c].[Nome_Localidade], [c].[Codigo_IBGE]" + _FROM)
SQL_CONTAGENS = garante_somente_leitura(  # E1: só agregados (total SP e do município do parâmetro)
    "SELECT COUNT(*) AS [total], SUM(CASE WHEN [c].[Codigo_IBGE] = %s THEN 1 ELSE 0 END) AS [municipio]" + _FROM)

_md = MetaData(schema="ref")
dne_logradouro = Table(
    "dne_logradouro", _md,
    Column("id", Integer, primary_key=True),
    Column("cep", CHAR(8), nullable=False, unique=True),
    Column("tipo_logradouro", String(80)),
    Column("logradouro", String(300)),
    Column("logradouro_abreviado", String(300)),
    Column("complemento", String(300)),
    Column("bairro", String(200)),
    Column("localidade", String(200)),
    Column("codigo_ibge", Integer),
    Column("uf", CHAR(2)),
    Column("id_logradouro", Integer),
    Column("hash", String(64)),
    Column("execucao_id", Integer),
    Column("criado_em", DateTime(timezone=True), server_default=func.now()),
    Column("atualizado_em", DateTime(timezone=True), server_default=func.now()),
)


def ibge_contagem() -> int:
    """Município da contagem de E1 (DNE_IBGE_CONTAGEM; padrão Franca). Não filtra a carga."""
    v = os.environ.get("DNE_IBGE_CONTAGEM", "").strip()
    return int(v) if v.isdigit() else IBGE_FRANCA


def cep8(v: Any) -> str | None:
    """Só dígitos e lpad 8; menos de 5 ou mais de 8 dígitos (ou zero) -> None."""
    if v is None or isinstance(v, bool):
        return None
    d = _NAO_DIGITO.sub("", str(v))
    if len(d) < 5 or len(d) > 8 or int(d) == 0:
        return None
    return d.zfill(8)


def _txt(v: Any, n: int) -> str | None:
    s = " ".join(str(v).split()) if v is not None else ""
    return s[:n] or None


def _int(v: Any) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def linha_ref(d: dict[str, Any]) -> dict[str, Any] | None:
    """Uma linha (logradouro + bairro + localidade) -> linha da referência. Sem CEP ou sem nome -> None (só bronze)."""
    cep = cep8(d.get("Cep"))
    nome = _txt(d.get("Nome_Logradouro"), 300)
    if not cep or not nome:
        return None
    usa_tipo = str(d.get("Utiliza_Tipo_Logradouro") or "").strip().upper() != "N"
    r = {"cep": cep, "tipo_logradouro": _txt(d.get("Tipo_Logradouro"), 80) if usa_tipo else None, "logradouro": nome,
         "logradouro_abreviado": _txt(d.get("Nome_Abreviado_Logradouro"), 300),
         "complemento": _txt(d.get("Complemento"), 300), "bairro": _txt(d.get("Nome_Bairro"), 200),
         "localidade": _txt(d.get("Nome_Localidade"), 200), "codigo_ibge": _int(d.get("Codigo_IBGE")),
         "uf": _txt(d.get("UF"), 2), "id_logradouro": _int(d.get("Id_Logradouro"))}
    r["hash"] = hash_registro(r)
    return r


def contagens(registros: list[dict[str, Any]], ibge: int | None = None) -> dict[str, int]:
    """E1: total SP e total do município (Codigo_IBGE). Só números."""
    ibge = ibge_contagem() if ibge is None else ibge
    return {"total_sp": len(registros), "municipio_ibge": ibge,
            "total_municipio": sum(1 for d in registros if _int(d.get("Codigo_IBGE")) == ibge)}


def importa(db: Session, registros: list[dict[str, Any]], execucao_id: int, tabela: str = TABELA) -> dict[str, int]:
    """E1 + E4: bronze primeiro (idempotente por hash) e depois upsert por CEP na referência."""
    if tabela != TABELA:
        raise ValueError("tabela_nao_permitida")
    cont = contagens(registros)
    log.info("dne_contagens total_sp=%d municipio_ibge=%d total_municipio=%d",
             cont["total_sp"], cont["municipio_ibge"], cont["total_municipio"])
    bronze = ingere(db, SISTEMA, tabela, "Id_Logradouro", None, registros, execucao_id)
    por_cep: dict[str, dict[str, Any]] = {}
    sem_cep = duplicados = 0
    linhas_por_cep: dict[str, int] = {}
    for d in registros:
        r = linha_ref(d)
        if r is None:
            sem_cep += 1
            continue
        linhas_por_cep[r["cep"]] = linhas_por_cep.get(r["cep"], 0) + 1
        atual = por_cep.get(r["cep"])
        if atual is not None:
            duplicados += 1
            if (atual["id_logradouro"] or 0) <= (r["id_logradouro"] or 0):
                continue  # vence o menor Id_Logradouro, independente da ordem da leitura
        por_cep[r["cep"]] = r
    colisoes_cep = sum(1 for n in linhas_por_cep.values() if n > 1)  # CEPs distintos que aparecem em 2+ linhas
    log.info("dne_colisoes colisoes_cep=%d linhas_descartadas=%d", colisoes_cep, duplicados)
    novos = atualizados = iguais = 0
    if por_cep:
        atuais = dict(db.execute(select(dne_logradouro.c.cep, dne_logradouro.c.hash)
                                 .where(dne_logradouro.c.cep.in_(list(por_cep)))).all())
        for cep, r in por_cep.items():
            if cep not in atuais:
                novos += 1
            elif atuais[cep] != r["hash"]:
                atualizados += 1
            else:
                iguais += 1
        mudou = [dict(r, execucao_id=execucao_id) for c, r in por_cep.items() if atuais.get(c) != r["hash"]]
        if mudou:
            stmt = insert(dne_logradouro).values(mudou)
            cols = [c for c in mudou[0] if c != "cep"]
            db.execute(stmt.on_conflict_do_update(
                index_elements=["cep"], set_={**{c: stmt.excluded[c] for c in cols}, "atualizado_em": func.now()}))
    db.commit()
    return {"origem": len(registros), "bronze_novos": bronze["novos"], "bronze_atualizados": bronze["atualizados"],
            "novos": novos, "atualizados": atualizados, "iguais": iguais, "sem_cep": sem_cep, "duplicados": duplicados,
            "colisoes_cep": colisoes_cep, **cont}


def reconcilia(db: Session, total_origem: int, tabela: str = TABELA) -> dict[str, Any]:
    """E1: contagem da origem x contagem de chaves distintas na bronze para a tabela do DNE."""
    n = db.scalar(select(func.count(func.distinct(RegistroBronze.id_origem)))
                  .where(RegistroBronze.sistema_origem == SISTEMA, RegistroBronze.tabela == tabela)) or 0
    return {"origem": total_origem, "bronze": n, "confere": n == total_origem}


def resolve(db: Session, cep: Any, logradouro: Any = None, bairro: Any = None) -> dict[str, Any]:
    """E2/E3: endereço pelo CEP no DNE. Não recebe (nem usa) o Codigo_Logradouro da PMF.

    Encontrado -> logradouro/bairro/localidade/uf do DNE, `valido_dne=True`.
    Não encontrado -> origem crua preservada, `valido_dne=False`, motivo `cep_nao_encontrado`.
    """
    c = cep8(cep)
    ref = db.execute(select(dne_logradouro).where(dne_logradouro.c.cep == c)).mappings().first() if c else None
    if ref is None:
        return {"cep": c, "logradouro": _txt(logradouro, 300), "bairro": _txt(bairro, 200), "localidade": None,
                "uf": None, "codigo_ibge": None, "valido_dne": False, "motivo": MOTIVO_CEP_NAO_ENCONTRADO}
    nome = " ".join(p for p in (ref["tipo_logradouro"], ref["logradouro"]) if p)
    return {"cep": c, "logradouro": nome, "bairro": ref["bairro"], "localidade": ref["localidade"], "uf": ref["uf"],
            "codigo_ibge": ref["codigo_ibge"], "valido_dne": True, "motivo": None}


def resolve_registro(db: Session, d: dict[str, Any]) -> dict[str, Any]:
    """Atalho para um registro de origem: só CEP, Logradouro e Bairro entram; qualquer código de logradouro é ignorado."""
    return resolve(db, d.get("CEP") or d.get("cep"), d.get("Logradouro") or d.get("logradouro") or d.get("Endereco"),
                   d.get("Bairro") or d.get("bairro"))
