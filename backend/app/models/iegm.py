"""IEG-M (Índice de Efetividade da Gestão Municipal, TCE-SP): faixa de cada índice por ano (migração 0039).

Uma linha por (ano base, índice, abrangência, faixa). Abrangência = código IBGE do município (Franca: 3516200), com
municipios = 1, ou "SP" (estado), com quantos municípios ficaram naquela faixa. O TCE publica no ano seguinte ao ano
base (ano_apuracao). Sem pessoa.
"""
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class FaixaIegm(Base):
    __tablename__ = "iegm_faixa"
    __table_args__ = (UniqueConstraint("ano_base", "indice", "abrangencia", "faixa", name="uq_iegm_faixa"), {"schema": "silver"})
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ano_base: Mapped[int] = mapped_column(Integer)
    ano_apuracao: Mapped[int] = mapped_column(Integer)
    indice: Mapped[str] = mapped_column(String(12))
    abrangencia: Mapped[str] = mapped_column(String(10))
    faixa: Mapped[str] = mapped_column(String(2))
    municipios: Mapped[int] = mapped_column(Integer)
    execucao_id: Mapped[int | None] = mapped_column(Integer)
    carregado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
