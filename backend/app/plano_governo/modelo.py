from datetime import datetime

from sqlalchemy import DateTime, Integer, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.models.controle import JSONType


class PlanoGovernoEstado(Base):
    __tablename__ = "plano_governo_estado"
    __table_args__ = {"schema": "controle"}
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    versao: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    conteudo: Mapped[dict] = mapped_column(JSONType, nullable=False)
    atualizado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
