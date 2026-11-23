"""Procedência exibida na API: o sistema de origem e a data que o ouro já guarda.

Nada é inventado. Sem data na linha, usa o maior `fonte_atualizado_em` do ouro daquele sistema.
"""
from datetime import date, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.camadas import DadoGold


def iso(v) -> str | None:
    if v is None:
        return None
    if isinstance(v, datetime | date):
        return v.isoformat()
    return str(v)


def carimbo(sistema: str, quando=None) -> dict:
    q = iso(quando)
    return {"sistema": sistema, "fonte": sistema, "fonte_atualizado_em": q, "atualizado_em": q}


def marco_ouro(db: Session, cid: str, sistema: str):
    """Maior data que a camada ouro guardou para este cidadão neste sistema."""
    return db.scalar(select(func.max(DadoGold.fonte_atualizado_em)).where(
        DadoGold.cidadao_id == cid, DadoGold.sistema == sistema))


_PREFIXOS = (
    ("saude_", "SAUDE"),
    ("trib_lancado", "FINANCEIRO"),
    ("trib_pago", "FINANCEIRO"),
    ("trib_inadimplencia", "FINANCEIRO"),
    ("trib_divida", "DIVIDA"),
    ("trib_devedores", "DIVIDA"),
    ("trib_saldo", "DIVIDA"),
    ("trib_maiores", "DIVIDA"),
    ("kpi", "OURO"),
)


def sistema_da_serie(serie: str) -> str:
    for prefixo, sistema in _PREFIXOS:
        if serie == prefixo or serie.startswith(prefixo):
            return sistema
    return "OURO"


FONTE_EVENTO = {
    "Saúde": "SAUDE", "IPTU": "IPTU", "Financeiro": "FINANCEIRO", "Dívida ativa": "DIVIDA",
    "Educação": "EDUCACAO", "Cemitério": "CEMITERIO", "SIL": "SIL",
}
