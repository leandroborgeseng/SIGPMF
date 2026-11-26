"""Fila de revisão: interessado do SEI sem CPF/CNPJ válido não vincula sozinho.

O nome fica em controle.sei_revisao_interessado. Processo não público não entra
aqui (a projeção pública é a única chamadora).
"""
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.identity.correlacao import normaliza_nome
from app.models.controle import SeiRevisaoInteressado


def _peso(v) -> float | None:
    try:
        return float(v) if v is not None and v != "" else None
    except (TypeError, ValueError):
        return None


def enfileira(db: Session, processos: list[dict], ambiente: str) -> int:
    """Inclui ou atualiza quem ficou sem documento. Devolve quantos nomes entraram na fila."""
    n = 0
    amb = (ambiente or "")[:20]
    for p in processos:
        if not isinstance(p, dict):
            continue
        idp = str(p.get("id_procedimento") or "")[:50]
        if not idp:
            continue
        prot = p.get("protocolo")
        prot = str(prot)[:80] if prot else None
        for i in p.get("interessados") or []:
            if not isinstance(i, dict):
                continue
            if _peso(i.get("peso")) == 1.0 and (i.get("cidadao_id") or i.get("empresa_id")):
                continue
            pend = i.get("pendencia")
            if pend not in ("revisao_nome", "sem_identificacao"):
                continue
            nome = (str(i.get("nome")).strip()[:300] or None) if i.get("nome") else None
            norm = (normaliza_nome(nome) or "(sem nome)")[:300]
            row = db.scalar(select(SeiRevisaoInteressado).where(
                SeiRevisaoInteressado.id_procedimento == idp,
                SeiRevisaoInteressado.nome_norm == norm,
                SeiRevisaoInteressado.ambiente_origem == amb))
            if row is None:
                db.add(SeiRevisaoInteressado(id_procedimento=idp, protocolo=prot, nome=nome, nome_norm=norm,
                                             pendencia=pend, ambiente_origem=amb))
            else:
                row.protocolo, row.nome, row.pendencia = prot, nome, pend
                row.atualizado_em = datetime.now(UTC)
            n += 1
    db.flush()
    return n
