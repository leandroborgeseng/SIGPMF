from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.models.controle import JSONType

S = {"schema": "controle"}


class ContagemExecucao(Base):
    __tablename__ = "contagem_execucao"
    __table_args__ = S
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    usuario: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20))
    codigo: Mapped[str | None] = mapped_column(String(50))
    limiar_cpf: Mapped[int] = mapped_column(Integer)
    iniciado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    terminado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ContagemResultado(Base):
    __tablename__ = "contagem_resultado"
    __table_args__ = S
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    execucao_id: Mapped[int] = mapped_column(ForeignKey("controle.contagem_execucao.id"))
    consulta: Mapped[str] = mapped_column(String(40))
    base: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(20))
    codigo: Mapped[str | None] = mapped_column(String(50))
    sql_hash: Mapped[str] = mapped_column(String(64))
    resultado: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONType)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ContagemCpfRepetido(Base):
    __tablename__ = "contagem_cpf_repetido"
    __table_args__ = S
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    execucao_id: Mapped[int] = mapped_column(ForeignKey("controle.contagem_execucao.id"))
    base: Mapped[str] = mapped_column(String(100))
    tabela: Mapped[str] = mapped_column(String(200))
    cpf: Mapped[str] = mapped_column(String(11))
    ocorrencias: Mapped[int] = mapped_column(Integer)
    limiar: Mapped[int] = mapped_column(Integer)
