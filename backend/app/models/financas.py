"""Finanças públicas pelo AUDESP (AU3, migração 0038): prata de despesa, receita e restos a pagar + controle dos arquivos.

A prata de cada (tipo, ano do arquivo) é a foto do último arquivo publicado pelo TCE: a carga troca as linhas daquele
ano inteiras (o TCE corrige e republica). Sem pessoa ligada: o AUDESP não traz CPF do credor (restos a pagar traz e o
conector descarta na leitura).
"""
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import BigInteger, Date, DateTime, ForeignKey, Index, Integer, Numeric, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

V = Numeric(18, 2)


class ArquivoAudesp(Base):
    """Último arquivo do AUDESP carregado por tipo e ano: assinatura do TCE (Last-Modified + tamanho) e SHA-256."""
    __tablename__ = "audesp_arquivos"
    __table_args__ = (UniqueConstraint("tipo", "ano", name="uq_audesp_arquivo_tipo_ano"), {"schema": "controle"})
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tipo: Mapped[str] = mapped_column(String(20))
    ano: Mapped[int] = mapped_column(Integer)
    origem: Mapped[str] = mapped_column(String(10))  # tce | upload
    assinatura: Mapped[str | None] = mapped_column(String(200))
    sha256: Mapped[str] = mapped_column(String(64))
    linhas: Mapped[int] = mapped_column(Integer)
    ultimo_mes: Mapped[int | None] = mapped_column(Integer)
    execucao_id: Mapped[int | None] = mapped_column(ForeignKey("controle.execucoes.id"))
    carregado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Despesa(Base):
    __tablename__ = "despesa"
    __table_args__ = (Index("ix_silver_despesa_ano_mes", "ano", "mes"), Index("ix_silver_despesa_cnpj", "credor_cnpj"),
                      {"schema": "silver"})
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    ano_arquivo: Mapped[int] = mapped_column(Integer)
    id_origem: Mapped[str] = mapped_column(String(40))
    ano: Mapped[int] = mapped_column(Integer)
    mes: Mapped[int] = mapped_column(Integer)
    orgao: Mapped[str | None] = mapped_column(String(200))
    fase: Mapped[str] = mapped_column(String(12))  # empenhado | liquidado | pago | anulado
    nr_empenho: Mapped[str | None] = mapped_column(String(30))
    dt_emissao: Mapped[date | None] = mapped_column(Date)
    credor_tipo: Mapped[str | None] = mapped_column(String(10))  # PJ | PF | especial
    credor_cnpj: Mapped[str | None] = mapped_column(String(14))
    credor_codigo_tce: Mapped[str | None] = mapped_column(String(30))
    credor_nome: Mapped[str | None] = mapped_column(String(300))
    funcao: Mapped[str | None] = mapped_column(String(120))
    subfuncao: Mapped[str | None] = mapped_column(String(160))
    cd_programa: Mapped[str | None] = mapped_column(String(10))
    programa: Mapped[str | None] = mapped_column(String(300))
    cd_acao: Mapped[str | None] = mapped_column(String(10))
    acao: Mapped[str | None] = mapped_column(String(300))
    fonte_recurso: Mapped[str | None] = mapped_column(String(160))
    aplicacao_codigo: Mapped[str | None] = mapped_column(String(4))
    aplicacao: Mapped[str | None] = mapped_column(String(300))
    modalidade_licitacao: Mapped[str | None] = mapped_column(String(60))
    elemento_codigo: Mapped[str | None] = mapped_column(String(8))
    elemento: Mapped[str | None] = mapped_column(String(300))
    natureza: Mapped[str | None] = mapped_column(String(15))
    valor: Mapped[Decimal] = mapped_column(V)


class Receita(Base):
    __tablename__ = "receita"
    __table_args__ = (Index("ix_silver_receita_ano_mes", "ano", "mes"), {"schema": "silver"})
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    ano_arquivo: Mapped[int] = mapped_column(Integer)
    id_origem: Mapped[str] = mapped_column(String(40))
    ano: Mapped[int] = mapped_column(Integer)
    mes: Mapped[int] = mapped_column(Integer)
    orgao: Mapped[str | None] = mapped_column(String(200))
    poder: Mapped[str | None] = mapped_column(String(30))
    fonte_recurso: Mapped[str | None] = mapped_column(String(160))
    aplicacao_codigo: Mapped[str | None] = mapped_column(String(4))
    aplicacao: Mapped[str | None] = mapped_column(String(300))
    aplicacao_variavel: Mapped[str | None] = mapped_column(String(300))
    categoria_codigo: Mapped[str | None] = mapped_column(String(8))
    categoria: Mapped[str | None] = mapped_column(String(200))
    subcategoria_codigo: Mapped[str | None] = mapped_column(String(8))
    subcategoria: Mapped[str | None] = mapped_column(String(200))
    origem_codigo: Mapped[str | None] = mapped_column(String(8))
    origem: Mapped[str | None] = mapped_column(String(200))
    d1_codigo: Mapped[str | None] = mapped_column(String(8))
    d1: Mapped[str | None] = mapped_column(String(200))
    d2_codigo: Mapped[str | None] = mapped_column(String(8))
    d2: Mapped[str | None] = mapped_column(String(200))
    d3_codigo: Mapped[str | None] = mapped_column(String(8))
    d3: Mapped[str | None] = mapped_column(String(200))
    natureza_codigo: Mapped[str | None] = mapped_column(String(8))
    natureza: Mapped[str | None] = mapped_column(String(300))
    valor: Mapped[Decimal] = mapped_column(V)


VALORES_RESTOS = ("inscrito_proc", "saldo_inicial_proc", "cancelado_proc", "pago_proc", "estorno_pago_proc",
                  "inscrito_nproc", "saldo_inicial_nproc", "liquidado_nproc", "estorno_liquidado_nproc", "cancelado_nproc",
                  "pago_nproc", "estorno_pago_nproc")


class RestoAPagar(Base):
    __tablename__ = "restos_a_pagar"
    __table_args__ = (Index("ix_silver_restos_ano_mes", "ano_balancete", "mes_balancete"), {"schema": "silver"})
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    ano_arquivo: Mapped[int] = mapped_column(Integer)
    id_origem: Mapped[str] = mapped_column(String(40))
    ano_balancete: Mapped[int] = mapped_column(Integer)
    mes_balancete: Mapped[int] = mapped_column(Integer)  # 13 = encerramento do exercício anterior
    dt_referencia: Mapped[date | None] = mapped_column(Date)
    orgao: Mapped[str | None] = mapped_column(String(200))
    nr_empenho: Mapped[str | None] = mapped_column(String(30))
    ano_empenho: Mapped[int | None] = mapped_column(Integer)
    modalidade_licitacao: Mapped[str | None] = mapped_column(String(60))
    funcao: Mapped[str | None] = mapped_column(String(160))
    subfuncao: Mapped[str | None] = mapped_column(String(160))
    fonte_recurso: Mapped[str | None] = mapped_column(String(160))
    aplicacao_codigo: Mapped[str | None] = mapped_column(String(4))
    elemento_codigo: Mapped[str | None] = mapped_column(String(8))
    elemento: Mapped[str | None] = mapped_column(String(300))
    natureza: Mapped[str | None] = mapped_column(String(15))
    credor_tipo: Mapped[str | None] = mapped_column(String(10))
    credor_cnpj: Mapped[str | None] = mapped_column(String(14))
    credor_nome: Mapped[str | None] = mapped_column(String(300))
    inscrito_proc: Mapped[Decimal] = mapped_column(V)
    saldo_inicial_proc: Mapped[Decimal] = mapped_column(V)
    cancelado_proc: Mapped[Decimal] = mapped_column(V)
    pago_proc: Mapped[Decimal] = mapped_column(V)
    estorno_pago_proc: Mapped[Decimal] = mapped_column(V)
    inscrito_nproc: Mapped[Decimal] = mapped_column(V)
    saldo_inicial_nproc: Mapped[Decimal] = mapped_column(V)
    liquidado_nproc: Mapped[Decimal] = mapped_column(V)
    estorno_liquidado_nproc: Mapped[Decimal] = mapped_column(V)
    cancelado_nproc: Mapped[Decimal] = mapped_column(V)
    pago_nproc: Mapped[Decimal] = mapped_column(V)
    estorno_pago_nproc: Mapped[Decimal] = mapped_column(V)
