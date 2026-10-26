from typing import Any

from sqlalchemy.orm import Session

from app.models.controle import Auditoria


def audita(db: Session, usuario: str | None, acao: str, alvo: str | None = None,
           detalhe: dict[str, Any] | None = None, ip: str | None = None, commit: bool = True) -> None:
    """Registro append-only (o banco bloqueia UPDATE/DELETE em controle.auditoria)."""
    db.add(Auditoria(usuario=usuario, acao=acao, alvo=alvo, detalhe=detalhe or {}, ip=ip))
    if commit:
        db.commit()
