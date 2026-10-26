from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

JSONType = JSON().with_variant(JSONB(), "postgresql")
S = {"schema": "controle"}


class Sistema(Base):
    __tablename__ = "sistemas"
    __table_args__ = S
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    codigo: Mapped[str] = mapped_column(String(50), unique=True)
    nome: Mapped[str] = mapped_column(String(200))
    descricao: Mapped[str | None] = mapped_column(Text)
    dominio: Mapped[str | None] = mapped_column(String(50))
    ativo: Mapped[bool] = mapped_column(Boolean, default=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TabelaCatalogo(Base):
    __tablename__ = "tabelas_catalogo"
    __table_args__ = (UniqueConstraint("sistema_id", "nome"), S)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sistema_id: Mapped[int] = mapped_column(ForeignKey("controle.sistemas.id", ondelete="CASCADE"))
    nome: Mapped[str] = mapped_column(String(200))
    chave: Mapped[str] = mapped_column(String(200))
    campo_incremental: Mapped[str | None] = mapped_column(String(200))
    ativo: Mapped[bool] = mapped_column(Boolean, default=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Execucao(Base):
    __tablename__ = "execucoes"
    __table_args__ = S
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tipo: Mapped[str] = mapped_column(String(50))
    sistema: Mapped[str | None] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(30))
    usuario: Mapped[str | None] = mapped_column(String(200))
    iniciado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finalizado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lidos: Mapped[int] = mapped_column(Integer, default=0)
    novos: Mapped[int] = mapped_column(Integer, default=0)
    atualizados: Mapped[int] = mapped_column(Integer, default=0)
    ignorados: Mapped[int] = mapped_column(Integer, default=0)
    lotes: Mapped[int] = mapped_column(Integer, default=0)
    duplicados: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    pendentes: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    mensagem: Mapped[str | None] = mapped_column(Text)


class Log(Base):
    __tablename__ = "logs"
    __table_args__ = S
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    execucao_id: Mapped[int | None] = mapped_column(ForeignKey("controle.execucoes.id"))
    nivel: Mapped[str] = mapped_column(String(10))
    mensagem: Mapped[str] = mapped_column(Text)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Usuario(Base):
    __tablename__ = "usuarios"
    __table_args__ = S
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(200), unique=True)
    nome: Mapped[str] = mapped_column(String(200))
    perfil: Mapped[str] = mapped_column(String(20))
    senha_hash: Mapped[str] = mapped_column(String(200))
    ativo: Mapped[bool] = mapped_column(Boolean, default=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Auditoria(Base):
    __tablename__ = "auditoria"
    __table_args__ = S
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    usuario: Mapped[str | None] = mapped_column(String(200))
    acao: Mapped[str] = mapped_column(String(50))
    alvo: Mapped[str | None] = mapped_column(String(300))
    detalhe: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    ip: Mapped[str | None] = mapped_column(String(64))
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Parametro(Base):
    __tablename__ = "parametros"
    __table_args__ = S
    chave: Mapped[str] = mapped_column(String(100), primary_key=True)
    valor: Mapped[str] = mapped_column(String(200))
    descricao: Mapped[str | None] = mapped_column(Text)


class ConfirmacaoObito(Base):
    """Confirmação humana (admin_lgpd) de que um óbito pertence a um cidadão. Sobrevive à reconstrução da silver."""
    __tablename__ = "confirmacoes_obito"
    __table_args__ = (UniqueConstraint("sistema", "tabela", "chave_origem"), S)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sistema: Mapped[str] = mapped_column(String(50))
    tabela: Mapped[str] = mapped_column(String(200))
    chave_origem: Mapped[str] = mapped_column(String(200))
    cidadao_id: Mapped[str] = mapped_column(String(36))
    usuario: Mapped[str] = mapped_column(String(200))
    motivo: Mapped[str] = mapped_column(Text)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class BaseCatalogo(Base):
    """Bases do servidor de origem examinadas pelo DBA (seed em app/catalogo/bases.py, migração 0005)."""
    __tablename__ = "bases_catalogo"
    __table_args__ = S
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    nome: Mapped[str] = mapped_column(String(100), unique=True)
    grupo: Mapped[str] = mapped_column(String(30))
    triagem: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20))
    prioridade: Mapped[int | None] = mapped_column(Integer)
    sistemas: Mapped[list[str]] = mapped_column(JSONType, default=list)
    conjunto: Mapped[str | None] = mapped_column(String(50))
    descricao: Mapped[str | None] = mapped_column(Text)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    atualizado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
