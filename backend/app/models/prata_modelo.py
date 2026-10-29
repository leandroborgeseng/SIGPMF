"""Modelo prata do PR-03 (0030): empresa única, ligação cidadão–empresa e procedência de cada valor (D3)."""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

SV = {"schema": "silver"}


class Empresa(Base):
    __tablename__ = "empresa"
    __table_args__ = (UniqueConstraint("fonte_inscricao", "inscricao_municipal", name="uq_silver_empresa_fonte_inscricao"),
                      SV)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cnpj: Mapped[str | None] = mapped_column(String(14))
    fonte_inscricao: Mapped[str | None] = mapped_column(String(50))
    inscricao_municipal: Mapped[str | None] = mapped_column(String(30))
    razao_social: Mapped[str | None] = mapped_column(String(300))
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CidadaoEmpresa(Base):
    __tablename__ = "cidadao_empresa"
    __table_args__ = (UniqueConstraint("cidadao_id", "empresa_id", "papel", name="uq_silver_cidadao_empresa"), SV)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cidadao_id: Mapped[str] = mapped_column(ForeignKey("silver.cidadaos.cidadao_id", ondelete="RESTRICT"))
    empresa_id: Mapped[int] = mapped_column(ForeignKey("silver.empresa.id", ondelete="RESTRICT"))
    papel: Mapped[str] = mapped_column(String(30))
    sistema: Mapped[str] = mapped_column(String(50))
    tabela: Mapped[str] = mapped_column(String(200))
    chave_hash: Mapped[str] = mapped_column(String(64))
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ProcedenciaValor(Base):
    __tablename__ = "procedencia_valor"
    __table_args__ = (UniqueConstraint("entidade", "entidade_id", "campo", "sistema", "tabela", "chave_hash",
                                       name="uq_silver_procedencia_origem"), SV)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    entidade: Mapped[str] = mapped_column(String(20))
    entidade_id: Mapped[str] = mapped_column(String(36))
    campo: Mapped[str] = mapped_column(String(60))
    valor: Mapped[str | None] = mapped_column(Text)
    completude: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    sistema: Mapped[str] = mapped_column(String(50))
    tabela: Mapped[str] = mapped_column(String(200))
    chave_hash: Mapped[str] = mapped_column(String(64))
    data_referencia: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    vencedor: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    registrado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
