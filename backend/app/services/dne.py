"""PR-02: base de CEP dos Correios (DNE) e resolução de endereço por CEP (E1-E4).

- E1: o arquivo/tabela DNE entra primeiro na bronze (`bronze.registros`, sistema CORREIOS) e a reconciliação
  compara a contagem da origem com a da bronze.
- E2: o logradouro de um endereço vem do DNE pelo CEP. O `Codigo_Logradouro` da PMF nunca é usado como chave (D6).
- E3: CEP ausente, lixo ou fora do DNE -> mantém a origem crua com o motivo `cep_nao_encontrado`, sem descartar.
- E4: um DNE novo atualiza `ref.dne_logradouro` por upsert no CEP, sem duplicar.

Os nomes de coluna do DNE seguem o layout padrão dos Correios (LOG_LOGRADOURO); a allowlist de tabelas
ainda será confirmada com o DBA. Nenhum valor de entrada vai para log ou mensagem de erro.
"""
import re
from typing import Any

from sqlalchemy import CHAR, Column, DateTime, Integer, MetaData, String, Table, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.bronze.ingestao import hash_registro, ingere
from app.models.camadas import RegistroBronze

SISTEMA = "CORREIOS"
TABELAS_PERMITIDAS = ("DNE_GU_SP_LOGRADOUROS", "LOG_LOGRADOURO_SP")  # a confirmar com o DBA
MOTIVO_CEP_NAO_ENCONTRADO = "cep_nao_encontrado"
_NAO_DIGITO = re.compile(r"\D")

_md = MetaData(schema="ref")
dne_logradouro = Table(
    "dne_logradouro", _md,
    Column("id", Integer, primary_key=True),
    Column("cep", CHAR(8), nullable=False, unique=True),
    Column("tipo_logradouro", String(80)),
    Column("logradouro", String(300), nullable=False),
    Column("bairro", String(200)),
    Column("localidade", String(200)),
    Column("uf", CHAR(2)),
    Column("dne_id", String(20)),
    Column("hash", String(64), nullable=False),
    Column("execucao_id", Integer),
    Column("criado_em", DateTime(timezone=True), server_default=func.now()),
    Column("atualizado_em", DateTime(timezone=True), server_default=func.now()),
)


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


def linha_ref(d: dict[str, Any]) -> dict[str, Any] | None:
    """Uma linha do DNE -> linha da referência. Sem CEP válido ou sem logradouro -> None (fica só na bronze)."""
    cep = cep8(d.get("CEP"))
    nome = _txt(d.get("LOG_NO"), 300)
    if not cep or not nome:
        return None
    r = {"cep": cep, "tipo_logradouro": _txt(d.get("TLO_TX"), 80), "logradouro": nome,
         "bairro": _txt(d.get("BAI_NO"), 200), "localidade": _txt(d.get("LOC_NO"), 200),
         "uf": _txt(d.get("UFE_SG"), 2), "dne_id": _txt(d.get("LOG_NU"), 20)}
    r["hash"] = hash_registro(r)
    return r


def importa(db: Session, tabela: str, registros: list[dict[str, Any]], execucao_id: int) -> dict[str, int]:
    """E1 + E4: bronze primeiro (idempotente por hash) e depois upsert por CEP na referência."""
    if tabela not in TABELAS_PERMITIDAS:
        raise ValueError("tabela_nao_permitida")
    chave = "LOG_NU" if registros and all(r.get("LOG_NU") is not None for r in registros) else "CEP"
    bronze = ingere(db, SISTEMA, tabela, chave, None, registros, execucao_id)
    por_cep: dict[str, dict[str, Any]] = {}
    sem_cep = 0
    for d in registros:
        r = linha_ref(d)
        if r is None:
            sem_cep += 1
            continue
        por_cep[r["cep"]] = r  # o último do arquivo vence dentro do lote (não duplica)
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
            cols = ("tipo_logradouro", "logradouro", "bairro", "localidade", "uf", "dne_id", "hash", "execucao_id")
            db.execute(stmt.on_conflict_do_update(
                index_elements=["cep"], set_={**{c: stmt.excluded[c] for c in cols}, "atualizado_em": func.now()}))
    db.commit()
    return {"origem": len(registros), "bronze_novos": bronze["novos"], "bronze_atualizados": bronze["atualizados"],
            "novos": novos, "atualizados": atualizados, "iguais": iguais, "sem_cep": sem_cep}


def reconcilia(db: Session, tabela: str, total_origem: int) -> dict[str, Any]:
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
                "uf": None, "valido_dne": False, "motivo": MOTIVO_CEP_NAO_ENCONTRADO}
    nome = " ".join(p for p in (ref["tipo_logradouro"], ref["logradouro"]) if p)
    return {"cep": c, "logradouro": nome, "bairro": ref["bairro"], "localidade": ref["localidade"], "uf": ref["uf"],
            "valido_dne": True, "motivo": None}


def resolve_registro(db: Session, d: dict[str, Any]) -> dict[str, Any]:
    """Atalho para um registro de origem: só CEP, Logradouro e Bairro entram; qualquer código de logradouro é ignorado."""
    return resolve(db, d.get("CEP") or d.get("cep"), d.get("Logradouro") or d.get("logradouro") or d.get("Endereco"),
                   d.get("Bairro") or d.get("bairro"))
