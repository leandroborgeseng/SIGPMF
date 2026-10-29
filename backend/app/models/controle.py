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

    def alerta_publico(self) -> dict | None:
        """Aviso sem falha (ex.: `tabela_vazia`), com código fixo e só nomes de tabela."""
        from app.services.tabelas_vazias import alerta
        if self.tipo == "releitura_deploy":
            from app.services.releitura_deploy import alerta as alerta_releitura
            return alerta_releitura(self)
        return alerta(self)

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


class SeiPaiPendente(Base):
    """0023 (SEI M4): tentativas de consulta por processo pai desconhecido (persistem entre execuções; teto 3)."""
    __tablename__ = "sei_pais_pendentes"
    __table_args__ = (UniqueConstraint("id_processo", "ambiente_origem", name="uq_sei_pai_pendente"), S)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    id_processo: Mapped[str] = mapped_column(String(50))
    ambiente_origem: Mapped[str] = mapped_column(String(20))
    tentativas: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    situacao: Mapped[str] = mapped_column(String(20), default="pendente", server_default="pendente")
    execucao_id: Mapped[int | None] = mapped_column(ForeignKey("controle.execucoes.id"))
    atualizado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AcaoUnica(Base):
    """Marcador de ação de uso único no boot (0026): `concluida` em (acao, fonte, token=SHA do deploy) impede repetir."""
    __tablename__ = "acoes_unicas"
    __table_args__ = (UniqueConstraint("acao", "fonte", "token", name="uq_acoes_unicas_acao_fonte_token"), S)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    acao: Mapped[str] = mapped_column(String(50))
    fonte: Mapped[str] = mapped_column(String(200))
    token: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(20))
    codigo: Mapped[str | None] = mapped_column(String(50))
    execucao_id: Mapped[int | None] = mapped_column(Integer)
    usuario: Mapped[str | None] = mapped_column(String(200))
    tentativas: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    atualizado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class UsuarioSecretaria(Base):
    """PR-06 (0033): vínculo do usuário à secretaria, com vigência. Remoção lógica (removido_em), nunca DELETE."""
    __tablename__ = "usuario_secretaria"
    __table_args__ = S
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    usuario_id: Mapped[int] = mapped_column(ForeignKey("controle.usuarios.id"))
    secretaria: Mapped[str] = mapped_column(String(40))
    perfil: Mapped[str | None] = mapped_column(String(20))
    vigencia_inicio: Mapped[date] = mapped_column(Date)
    vigencia_fim: Mapped[date | None] = mapped_column(Date)
    cadastrado_por: Mapped[str] = mapped_column(String(200))
    cadastrado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    atualizado_por: Mapped[str | None] = mapped_column(String(200))
    atualizado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    removido_por: Mapped[str | None] = mapped_column(String(200))
    removido_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    motivo_remocao: Mapped[str | None] = mapped_column(Text)
    versao: Mapped[int] = mapped_column(Integer, default=1, server_default="1")


class AuditoriaAcesso(Base):
    """PR-06 (0033): uma linha por consulta nominal liberada pelo vínculo à secretaria. Somente inserção."""
    __tablename__ = "auditoria_acesso"
    __table_args__ = S
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    usuario: Mapped[str] = mapped_column(String(200))
    secretaria: Mapped[str] = mapped_column(String(40))
    recurso: Mapped[str] = mapped_column(String(50))
    alvo: Mapped[str | None] = mapped_column(String(300))
    ip: Mapped[str | None] = mapped_column(String(64))
    revelou_cpf: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SecretariaRef(Base):
    """PR-06 (0033): tabela de referência das secretarias, ajustável pela TI sem deploy."""
    __tablename__ = "secretarias"
    __table_args__ = S
    codigo: Mapped[str] = mapped_column(String(40), primary_key=True)
    nome: Mapped[str] = mapped_column(String(200))
    ativo: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    a_confirmar: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    obs: Mapped[str | None] = mapped_column(Text)
    atualizado_por: Mapped[str | None] = mapped_column(String(200))
    atualizado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DominioSecretaria(Base):
    """PR-06 (0033): domínio do ouro -> secretaria dona do dado nominal (a_confirmar = valor inicial pendente)."""
    __tablename__ = "dominio_secretaria"
    __table_args__ = S
    dominio: Mapped[str] = mapped_column(String(50), primary_key=True)
    secretaria: Mapped[str] = mapped_column(ForeignKey("controle.secretarias.codigo"))
    a_confirmar: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    obs: Mapped[str | None] = mapped_column(Text)
    atualizado_por: Mapped[str | None] = mapped_column(String(200))
    atualizado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), server_default=func.now())
