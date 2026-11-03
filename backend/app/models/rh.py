"""RH pelo Portal da Transparência (RH1, migração 0040): remuneração por matrícula e mês + controle dos meses carregados.

Nome e matrícula ficam na prata para o cruzamento com o Senior (matrícula = numcad; CPF vem de lá). A API do painel só
devolve agregados, com supressão de grupos pequenos; o portal, que é público, continua sendo o lugar da consulta por pessoa.
"""
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import BigInteger, Date, DateTime, ForeignKey, Index, Integer, Numeric, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

V = Numeric(14, 2)


class MesRh(Base):
    __tablename__ = "rh_meses"
    __table_args__ = (UniqueConstraint("ano", "mes", name="uq_rh_mes"), {"schema": "controle"})
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ano: Mapped[int] = mapped_column(Integer)
    mes: Mapped[int] = mapped_column(Integer)  # 13 = décimo terceiro
    sha256: Mapped[str | None] = mapped_column(String(64))
    linhas: Mapped[int] = mapped_column(Integer)  # 0 = o portal não tem o mês
    execucao_id: Mapped[int | None] = mapped_column(ForeignKey("controle.execucoes.id"))
    carregado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Remuneracao(Base):
    __tablename__ = "rh_remuneracao"
    __table_args__ = (Index("ix_silver_rh_ano_mes", "ano", "mes"), Index("ix_silver_rh_matricula", "matricula"),
                      Index("ix_silver_rh_secretaria", "secretaria"), {"schema": "silver"})
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    ano: Mapped[int] = mapped_column(Integer)
    mes: Mapped[int] = mapped_column(Integer)
    matricula: Mapped[str] = mapped_column(String(20))
    nome: Mapped[str | None] = mapped_column(String(200))
    situacao: Mapped[str | None] = mapped_column(String(30))
    cargo: Mapped[str | None] = mapped_column(String(120))
    funcao_inativa: Mapped[str | None] = mapped_column(String(120))
    secretaria: Mapped[str] = mapped_column(String(80))  # padronizada (os nomes mudam ao longo dos anos)
    secretaria_origem: Mapped[str | None] = mapped_column(String(120))
    grupo: Mapped[str] = mapped_column(String(12))  # ativa | inativos | outros
    jornada: Mapped[str | None] = mapped_column(String(10))
    referencia: Mapped[str | None] = mapped_column(String(20))
    salario_base: Mapped[Decimal] = mapped_column(V)
    vencimentos: Mapped[Decimal] = mapped_column(V)
    descontos: Mapped[Decimal] = mapped_column(V)
    liquido: Mapped[Decimal] = mapped_column(V)
    admissao: Mapped[date | None] = mapped_column(Date)
    afastamento: Mapped[date | None] = mapped_column(Date)
    demissao: Mapped[date | None] = mapped_column(Date)
