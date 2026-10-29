from datetime import date, datetime
from typing import Any

from sqlalchemy import JSON, Boolean, Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
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
    # Motivo seguro da falha (0007): sem valores, host, usuário ou senha
    erro_classe: Mapped[str | None] = mapped_column(String(80))
    erro_numero: Mapped[int | None] = mapped_column(Integer)
    erro_tabela: Mapped[str | None] = mapped_column(String(200))
    erro_objeto: Mapped[str | None] = mapped_column(String(200))
    erro_local: Mapped[str | None] = mapped_column(String(200))
    # carga completa (0008): progresso, pausa e pedido de cancelamento
    total_origem: Mapped[int | None] = mapped_column(Integer)
    tabela_atual: Mapped[str | None] = mapped_column(String(200))
    pausa_motivo: Mapped[str | None] = mapped_column(Text)
    cancelar: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    # etapas da carga completa (0009): leitura -> prata -> ouro -> concluida, contador e sinal de vida
    etapa: Mapped[str | None] = mapped_column(String(20))
    processadas_etapa: Mapped[int | None] = mapped_column(Integer)
    total_etapa: Mapped[int | None] = mapped_column(Integer)
    atualizado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # IN2 (0012): 'completa' (relê tudo) ou 'incremental' (só o alterado desde a marca d'água); nulo = completa
    modo: Mapped[str | None] = mapped_column(String(20))
    # SEI7 (0022): ambiente de onde a execução leu ("oficial" ou "homologacao"); nulo nos conectores sem essa noção
    ambiente_origem: Mapped[str | None] = mapped_column(String(20))

    def registra_erro(self, exc: BaseException) -> None:
        from app.services.diagnostico import categoria
        c = categoria(exc)
        self.erro_classe, self.erro_numero, self.erro_tabela = c["classe"], c["numero"], c["tabela"]
        self.erro_objeto, self.erro_local = c["objeto"], c["local"]

    def erro_publico(self) -> dict | None:
        if self.status != "falha" or not self.erro_classe:
            return None
        from app.services.diagnostico import motivo
        return {"classe": self.erro_classe, "numero": self.erro_numero, "tabela": self.erro_tabela,
                "objeto": self.erro_objeto, "local": self.erro_local,
                "motivo": motivo(self.erro_classe, self.erro_numero, self.erro_tabela, self.erro_objeto)}


class CheckpointCarga(Base):
    """Posição já gravada na bronze por (sistema, tabela): a carga completa retoma daqui sem duplicar."""
    __tablename__ = "checkpoints_carga"
    __table_args__ = (UniqueConstraint("sistema", "tabela", "ambiente", name="uq_checkpoint_sistema_tabela_ambiente"), S)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sistema: Mapped[str] = mapped_column(String(50))
    tabela: Mapped[str] = mapped_column(String(200))
    # 0021: producao|sandbox. Checkpoint da homologação nunca move o da oficial (SIL); demais sistemas: producao.
    ambiente: Mapped[str] = mapped_column(String(20), default="producao", server_default="producao")
    posicao: Mapped[int] = mapped_column(Integer, default=0)  # linhas já gravadas (progresso)
    ultima_chave: Mapped[list | None] = mapped_column(JSONType)  # keyset: último valor da chave gravado
    total_origem: Mapped[int | None] = mapped_column(Integer)
    concluida: Mapped[bool] = mapped_column(Boolean, default=False)
    execucao_id: Mapped[int | None] = mapped_column(ForeignKey("controle.execucoes.id"))
    atualizado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


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


class Agendamento(Base):
    """AG1: horário diário da carga completa de um conector (migração 0011). Um por vez, dentro da janela."""
    __tablename__ = "agendamentos"
    __table_args__ = S
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    conector: Mapped[str] = mapped_column(String(50), unique=True)
    horario: Mapped[str] = mapped_column(String(5))
    ativo: Mapped[bool] = mapped_column(Boolean, default=False)
    ordem: Mapped[int] = mapped_column(Integer, default=0)
    forcar: Mapped[bool] = mapped_column(Boolean, default=False)
    ultima_data: Mapped[date | None] = mapped_column(Date)
    tentativas: Mapped[int] = mapped_column(Integer, default=0)
    proxima_tentativa_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ultimo_status: Mapped[str | None] = mapped_column(String(30))
    ultima_mensagem: Mapped[str | None] = mapped_column(Text)
    ultima_execucao_id: Mapped[int | None] = mapped_column(ForeignKey("controle.execucoes.id"))
    ultimo_inicio_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ultimo_fim_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    assinatura_origem: Mapped[dict | None] = mapped_column(JSONType)
    ultima_completa_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # IN3 (0012)
    atualizado_por: Mapped[str | None] = mapped_column(String(200))
    atualizado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CamadasEstado(Base):
    """IN1 (0012): assinatura das entradas (bronze, confirmações, parâmetros, regras) da última prata/ouro."""
    __tablename__ = "camadas_estado"
    __table_args__ = S
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    assinatura: Mapped[dict] = mapped_column(JSONType)
    execucao_id: Mapped[int | None] = mapped_column(ForeignKey("controle.execucoes.id"))
    reconstruido_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
