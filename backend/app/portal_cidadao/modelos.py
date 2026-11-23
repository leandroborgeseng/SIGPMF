from datetime import datetime

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AcessoCidadao(Base):
    """Desafios, limites e sessões efêmeros. Nunca guarda CPF, respostas ou código em claro."""
    __tablename__ = "portal_acessos"
    __table_args__ = {"schema": "controle"}
    chave: Mapped[str] = mapped_column(String(64), primary_key=True)
    tipo: Mapped[str] = mapped_column(String(16), index=True)
    cidadao_id: Mapped[str | None] = mapped_column(String(36))
    segredo_hash: Mapped[str | None] = mapped_column(String(64))
    tentativas: Mapped[int] = mapped_column(Integer, default=0)
    expira_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
