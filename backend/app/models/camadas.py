"""Tabelas das camadas bronze, silver e gold."""
from datetime import date, datetime
from typing import Any

from sqlalchemy import Date, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.models.controle import JSONType


class RegistroBronze(Base):
    __tablename__ = "registros"
    __table_args__ = (UniqueConstraint("sistema_origem", "tabela", "id_origem", "hash"), {"schema": "bronze"})
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sistema_origem: Mapped[str] = mapped_column(String(50))
    tabela: Mapped[str] = mapped_column(String(200))
    id_origem: Mapped[str] = mapped_column(String(200))
    dados: Mapped[dict[str, Any]] = mapped_column(JSONType)
    hash: Mapped[str] = mapped_column(String(64))
    execucao_id: Mapped[int] = mapped_column(ForeignKey("controle.execucoes.id"))
    atualizado_origem_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    extraido_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


SV = {"schema": "silver"}


class Cidadao(Base):
    __tablename__ = "cidadaos"
    __table_args__ = SV
    cidadao_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    nome: Mapped[str] = mapped_column(String(300))
    data_nascimento: Mapped[date | None] = mapped_column(Date)
    atualizado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    falecido: Mapped[bool] = mapped_column(default=False, server_default="false")
    data_obito: Mapped[date | None] = mapped_column(Date)
    obito_fonte: Mapped[str | None] = mapped_column(String(100))


class Identificador(Base):
    __tablename__ = "identificadores"
    __table_args__ = (UniqueConstraint("tipo", "valor"), SV)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cidadao_id: Mapped[str] = mapped_column(ForeignKey("silver.cidadaos.cidadao_id", ondelete="CASCADE"))
    tipo: Mapped[str] = mapped_column(String(20))
    valor: Mapped[str] = mapped_column(String(50))


class SistemaSilver(Base):
    __tablename__ = "sistemas"
    __table_args__ = SV
    codigo: Mapped[str] = mapped_column(String(50), primary_key=True)
    nome: Mapped[str] = mapped_column(String(200))
    dominio: Mapped[str | None] = mapped_column(String(50))


class Vinculo(Base):
    __tablename__ = "vinculos"
    __table_args__ = (UniqueConstraint("sistema", "tabela", "chave_origem"), SV)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cidadao_id: Mapped[str] = mapped_column(ForeignKey("silver.cidadaos.cidadao_id", ondelete="CASCADE"))
    sistema: Mapped[str] = mapped_column(String(50))
    tabela: Mapped[str] = mapped_column(String(200))
    chave_origem: Mapped[str] = mapped_column(String(200))
    bronze_id: Mapped[int] = mapped_column(Integer)
    metodo: Mapped[str] = mapped_column(String(30))
    confianca: Mapped[float] = mapped_column(Float)
    atributos: Mapped[list[str]] = mapped_column(JSONType, default=list)
    fonte_atualizado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Endereco(Base):
    __tablename__ = "enderecos"
    __table_args__ = SV
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cidadao_id: Mapped[str] = mapped_column(ForeignKey("silver.cidadaos.cidadao_id", ondelete="CASCADE"))
    logradouro: Mapped[str | None] = mapped_column(String(300))
    bairro: Mapped[str | None] = mapped_column(String(200))
    cep: Mapped[str | None] = mapped_column(String(10))
    sistema: Mapped[str] = mapped_column(String(50))
    fonte_atualizado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    mais_recente: Mapped[bool] = mapped_column(default=False)


class CorrelacaoPendente(Base):
    __tablename__ = "correlacoes_pendentes"
    __table_args__ = (UniqueConstraint("sistema", "tabela", "chave_origem"), SV)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sistema: Mapped[str] = mapped_column(String(50))
    tabela: Mapped[str] = mapped_column(String(200))
    chave_origem: Mapped[str] = mapped_column(String(200))
    bronze_id: Mapped[int] = mapped_column(Integer)
    motivo: Mapped[str] = mapped_column(String(50))
    nome: Mapped[str | None] = mapped_column(String(300))
    candidatos: Mapped[list[str]] = mapped_column(JSONType, default=list)
    detalhe: Mapped[dict[str, Any] | None] = mapped_column(JSONType)
    status: Mapped[str] = mapped_column(String(20), default="pendente")


class Inconsistencia(Base):
    __tablename__ = "inconsistencias"
    __table_args__ = SV
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cidadao_id: Mapped[str | None] = mapped_column(String(36))
    sistema: Mapped[str] = mapped_column(String(50))
    tabela: Mapped[str] = mapped_column(String(200))
    chave_origem: Mapped[str] = mapped_column(String(200))
    tipo: Mapped[str] = mapped_column(String(50))
    campo: Mapped[str | None] = mapped_column(String(100))
    detalhe: Mapped[str | None] = mapped_column(Text)


class DadoGold(Base):
    """Visão por cidadão e domínio, cada valor com fonte e data (divergências preservadas)."""
    __tablename__ = "cidadao_dados"
    __table_args__ = {"schema": "gold"}
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cidadao_id: Mapped[str] = mapped_column(String(36), index=True)
    dominio: Mapped[str] = mapped_column(String(50))
    campo: Mapped[str] = mapped_column(String(100))
    valor: Mapped[str | None] = mapped_column(Text)
    sistema: Mapped[str] = mapped_column(String(50))
    fonte_atualizado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    mais_recente: Mapped[bool] = mapped_column(default=False)


class CidadaoSistemas(Base):
    __tablename__ = "cidadao_sistemas"
    __table_args__ = {"schema": "gold"}
    cidadao_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    nome: Mapped[str] = mapped_column(String(300))
    sistemas: Mapped[list[str]] = mapped_column(JSONType, default=list)
    dominios: Mapped[list[str]] = mapped_column(JSONType, default=list)
    atualizado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
