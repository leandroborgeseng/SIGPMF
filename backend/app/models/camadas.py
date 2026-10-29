"""Tabelas das camadas bronze, silver e gold."""
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
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
    # CC9 (0009): linha guardada em quarentena (ex.: "chave_nula"); prata e ouro ignoram
    quarentena: Mapped[str | None] = mapped_column(String(40))
    quarentena_campos: Mapped[list | None] = mapped_column(JSONType)


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
    __table_args__ = (UniqueConstraint("tipo", "valor"), Index("ix_silver_identificadores_cidadao_id", "cidadao_id"), SV)
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
    __table_args__ = (UniqueConstraint("sistema", "tabela", "chave_origem"),
                      Index("ix_silver_vinculos_cidadao_id_id", "cidadao_id", "id"), SV)  # 0010
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
    __table_args__ = (Index("ix_silver_enderecos_cidadao_id", "cidadao_id"), SV)
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


class Municipe(Base):
    """ISS.Municipes deduplicado: 1 linha por documento normalizado (M1). Empresas (J) nunca têm cidadao_id (M3).
    Tabela própria do conector: não é apagada pela reconstrução da silver."""
    __tablename__ = "municipes"
    __table_args__ = SV
    documento: Mapped[str] = mapped_column(String(14), primary_key=True)
    tipo: Mapped[str] = mapped_column(String(10))                 # cidadao | empresa
    tipo_pessoa: Mapped[str] = mapped_column(String(1))
    nome: Mapped[str | None] = mapped_column(String(300))
    nome_mae: Mapped[str | None] = mapped_column(String(300))
    data_nascimento: Mapped[date | None] = mapped_column(Date)
    pis_pasep: Mapped[str | None] = mapped_column(String(20))
    data_alteracao: Mapped[datetime | None] = mapped_column(DateTime(timezone=False))
    cidadao_id: Mapped[str | None] = mapped_column(String(36), index=True)
    confianca: Mapped[float | None] = mapped_column(Float)
    hash: Mapped[str] = mapped_column(String(64))
    execucao_id: Mapped[int] = mapped_column(ForeignKey("controle.execucoes.id"))


class MunicipeVersao(Base):
    """Versões divergentes do mesmo documento no ISS (sem PK, linhas repetidas): preservadas (M1)."""
    __tablename__ = "municipes_versoes"
    __table_args__ = (UniqueConstraint("documento", "hash"), SV)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    documento: Mapped[str] = mapped_column(String(14), index=True)
    hash: Mapped[str] = mapped_column(String(64))
    dados: Mapped[dict[str, Any]] = mapped_column(JSONType)
    execucao_id: Mapped[int] = mapped_column(ForeignKey("controle.execucoes.id"))


class MunicipePendencia(Base):
    """Linhas do ISS sem documento, com DV inválido ou Tipo_Pessoa R/nulo (M2/M4). Sem cidadao_id."""
    __tablename__ = "municipes_pendencias"
    __table_args__ = SV
    chave: Mapped[str] = mapped_column(String(32), primary_key=True)
    motivo: Mapped[str] = mapped_column(String(50))
    tipo_pessoa: Mapped[str | None] = mapped_column(String(1))
    nome: Mapped[str | None] = mapped_column(String(300))
    documento_mascarado: Mapped[str | None] = mapped_column(String(20))  # M10: só a máscara, nunca em claro
    status: Mapped[str] = mapped_column(String(20), default="pendente")
    execucao_id: Mapped[int] = mapped_column(ForeignKey("controle.execucoes.id"))


# ---------------- Tributário (onda 2, TR4): fatos da prata, reconstruídos de forma determinística da bronze ----------
# cidadao_id só quando o CPF bate com um cidadão já correlacionado (nunca cria cidadão); documento fica mascarável.
TR = {"schema": "silver"}


class Imovel(Base):
    __tablename__ = "imoveis"
    __table_args__ = TR
    codi: Mapped[str] = mapped_column(String(22), primary_key=True)
    inscricao: Mapped[str | None] = mapped_column(String(30))
    numero: Mapped[str | None] = mapped_column(String(20))
    complemento: Mapped[str | None] = mapped_column(String(200))
    cd_bairro: Mapped[int | None] = mapped_column(Integer)
    quadra: Mapped[str | None] = mapped_column(String(200))
    lote: Mapped[str | None] = mapped_column(String(200))
    area_terreno: Mapped[float | None] = mapped_column(Float)
    iptu_anual: Mapped[float | None] = mapped_column(Numeric(14, 2))
    situacao: Mapped[str | None] = mapped_column(String(20))
    atualizado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ImovelPossuidor(Base):
    __tablename__ = "imovel_possuidores"
    __table_args__ = (Index("ix_silver_possuidor_cidadao", "cidadao_id"), Index("ix_silver_possuidor_codi", "codi"), TR)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    codi: Mapped[str] = mapped_column(String(22))
    documento: Mapped[str] = mapped_column(String(20))  # só dígitos
    tipo_pessoa: Mapped[str] = mapped_column(String(2))  # PF / PJ
    cidadao_id: Mapped[str | None] = mapped_column(String(36))
    tipo_posse: Mapped[int | None] = mapped_column(Integer)
    desde: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ate: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ativo: Mapped[bool] = mapped_column(Boolean)


class EmpresaSil(Base):
    """Empresa da API SIL (tributário novo), pelo CCM. Liga ao cidadão só quando o CNPJ/CPF é um CPF já correlacionado
    (MEI, autônomo, empresário individual); PJ fica só com o documento (mascarado na API)."""
    __tablename__ = "empresas_sil"
    __table_args__ = (Index("ix_silver_empresa_sil_cidadao", "cidadao_id"), Index("ix_silver_empresa_sil_doc", "documento"),
                      TR)
    ccm: Mapped[int] = mapped_column(Integer, primary_key=True)
    documento: Mapped[str | None] = mapped_column(String(14))  # só dígitos
    tipo_pessoa: Mapped[str | None] = mapped_column(String(2))  # PF / PJ
    cidadao_id: Mapped[str | None] = mapped_column(String(36))
    nome: Mapped[str | None] = mapped_column(String(250))
    possui_debito: Mapped[bool | None] = mapped_column(Boolean)
    endereco: Mapped[str | None] = mapped_column(String(300))
    bairro: Mapped[str | None] = mapped_column(String(120))
    cep: Mapped[str | None] = mapped_column(String(12))


class Lancamento(Base):
    __tablename__ = "lancamentos"
    __table_args__ = (Index("ix_silver_lancamento_cidadao", "cidadao_id"), Index("ix_silver_lancamento_venc", "vencimento"), TR)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chave_origem: Mapped[str] = mapped_column(String(200), unique=True)
    exercicio: Mapped[int] = mapped_column(Integer)
    tributo: Mapped[str | None] = mapped_column(String(10))
    receita: Mapped[str | None] = mapped_column(String(10))
    parcela: Mapped[int | None] = mapped_column(Integer)
    contribuinte: Mapped[str | None] = mapped_column(String(20))
    codi: Mapped[str | None] = mapped_column(String(22))  # quando o contribuinte é uma inscrição imobiliária
    cidadao_id: Mapped[str | None] = mapped_column(String(36))
    vencimento: Mapped[date | None] = mapped_column(Date)
    valor: Mapped[float | None] = mapped_column(Numeric(14, 2))
    valor_pago: Mapped[float | None] = mapped_column(Numeric(14, 2))
    pagamento: Mapped[date | None] = mapped_column(Date)
    pago: Mapped[bool] = mapped_column(Boolean)
    status: Mapped[str | None] = mapped_column(String(50))


class DividaAtiva(Base):
    __tablename__ = "dividas"
    __table_args__ = (Index("ix_silver_divida_cidadao", "cidadao_id"), Index("ix_silver_divida_codi", "codi"), TR)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chave_origem: Mapped[str] = mapped_column(String(200), unique=True)
    codi: Mapped[str] = mapped_column(String(22))
    ano: Mapped[int | None] = mapped_column(Integer)
    natureza: Mapped[int | None] = mapped_column(Integer)
    cidadao_id: Mapped[str | None] = mapped_column(String(36))
    inscricao: Mapped[date | None] = mapped_column(Date)
    vencimento: Mapped[date | None] = mapped_column(Date)
    valor_origem: Mapped[float | None] = mapped_column(Numeric(14, 2))
    saldo: Mapped[float | None] = mapped_column(Numeric(14, 2))
    pago_total: Mapped[float | None] = mapped_column(Numeric(14, 2))
    status: Mapped[str | None] = mapped_column(String(5))
    situacao: Mapped[int | None] = mapped_column(Integer)
    protestos: Mapped[int | None] = mapped_column(Integer)
    processo: Mapped[str | None] = mapped_column(String(30))
    extinta: Mapped[bool] = mapped_column(Boolean)


# ---------------- Saúde, fatos (onda 3, SA-F2): sem conteúdo clínico; cidadão pela matrícula já correlacionada --------
class UnidadeSaude(Base):
    __tablename__ = "unidades_saude"
    __table_args__ = TR
    codigo: Mapped[int] = mapped_column(Integer, primary_key=True)
    nome: Mapped[str | None] = mapped_column(String(250))
    tipo: Mapped[str | None] = mapped_column(String(5))
    ativa: Mapped[bool] = mapped_column(Boolean)
    cd_bairro: Mapped[int | None] = mapped_column(Integer)


class Atendimento(Base):
    __tablename__ = "atendimentos"
    __table_args__ = (Index("ix_silver_atendimento_cidadao", "cidadao_id"), Index("ix_silver_atendimento_data", "data"), TR)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chave_origem: Mapped[str] = mapped_column(String(200), unique=True)
    matricula: Mapped[str | None] = mapped_column(String(30))
    cidadao_id: Mapped[str | None] = mapped_column(String(36))
    data: Mapped[date | None] = mapped_column(Date)
    unidade: Mapped[int | None] = mapped_column(Integer)
    especialidade: Mapped[int | None] = mapped_column(Integer)


class Agendamento(Base):
    __tablename__ = "agendamentos"
    __table_args__ = (Index("ix_silver_agendamento_cidadao", "cidadao_id"), Index("ix_silver_agendamento_data", "data_consulta"), TR)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chave_origem: Mapped[str] = mapped_column(String(200), unique=True)
    matricula: Mapped[str | None] = mapped_column(String(30))
    cidadao_id: Mapped[str | None] = mapped_column(String(36))
    data_consulta: Mapped[date | None] = mapped_column(Date)
    data_marcacao: Mapped[date | None] = mapped_column(Date)
    dias_espera: Mapped[int | None] = mapped_column(Integer)
    unidade: Mapped[int | None] = mapped_column(Integer)
    especialidade: Mapped[int | None] = mapped_column(Integer)


class Vacinacao(Base):
    __tablename__ = "vacinacoes"
    __table_args__ = (Index("ix_silver_vacinacao_cidadao", "cidadao_id"), Index("ix_silver_vacinacao_data", "data"), TR)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chave_origem: Mapped[str] = mapped_column(String(200), unique=True)
    matricula: Mapped[str | None] = mapped_column(String(30))
    cidadao_id: Mapped[str | None] = mapped_column(String(36))
    data: Mapped[date | None] = mapped_column(Date)
    unidade: Mapped[int | None] = mapped_column(Integer)
    vacina: Mapped[str | None] = mapped_column(String(60))
    dose: Mapped[str | None] = mapped_column(String(10))


class Evento(Base):
    """Linha do tempo do cidadão (onda 3, SA-F3): um fato por linha, de qualquer área, com fonte."""
    __tablename__ = "eventos"
    __table_args__ = (Index("ix_silver_evento_cidadao_data", "cidadao_id", "data"), TR)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cidadao_id: Mapped[str] = mapped_column(String(36))
    data: Mapped[date] = mapped_column(Date)
    area: Mapped[str] = mapped_column(String(20))
    tipo: Mapped[str] = mapped_column(String(40))
    titulo: Mapped[str] = mapped_column(String(300))
    fonte: Mapped[str] = mapped_column(String(50))


class Indicador(Base):
    """Ouro agregado (onda 3, SA-F4): série, período (AAAA-MM ou vazio), dimensão e valor. Sem pessoa."""
    __tablename__ = "indicadores"
    __table_args__ = (Index("ix_gold_indicador_serie", "serie", "periodo"), {"schema": "gold"})
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    serie: Mapped[str] = mapped_column(String(60))
    periodo: Mapped[str] = mapped_column(String(7), default="")
    dim: Mapped[str] = mapped_column(String(200), default="")
    valor: Mapped[float] = mapped_column(Float)
    atualizado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ---------------- Educação (FRA_GestaoEscolar + FRA_CoreSSO, GE6–GE11): prata própria, sem DELETE -----------------
# Reconstruída por upsert a partir da bronze (app.silver.educacao). Situação 3 = exclusão lógica (`excluido`), nunca
# apagada; todas as situações ficam no histórico e a ficha mostra como atual só a matrícula com situação 1.
class EscolaAluno(Base):
    __tablename__ = "escola_alunos"
    __table_args__ = (Index("ix_silver_escola_aluno_cidadao", "cidadao_id"), SV)
    alu_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    pes_id: Mapped[str | None] = mapped_column(String(36))
    situacao: Mapped[int | None] = mapped_column(Integer)
    excluido: Mapped[bool] = mapped_column(Boolean, default=False)
    nome: Mapped[str | None] = mapped_column(String(300))
    nome_social: Mapped[str | None] = mapped_column(String(300))
    data_nascimento: Mapped[date | None] = mapped_column(Date)
    nome_mae: Mapped[str | None] = mapped_column(String(300))
    cpf: Mapped[str | None] = mapped_column(String(11))
    nis: Mapped[str | None] = mapped_column(String(11))
    certidao: Mapped[str | None] = mapped_column(String(40))
    cep: Mapped[str | None] = mapped_column(String(10))
    logradouro: Mapped[str | None] = mapped_column(String(300))
    numero: Mapped[str | None] = mapped_column(String(30))
    complemento: Mapped[str | None] = mapped_column(String(200))
    bairro: Mapped[str | None] = mapped_column(String(200))
    cidadao_id: Mapped[str | None] = mapped_column(String(36))
    confianca: Mapped[float | None] = mapped_column(Float)
    status_vinculo: Mapped[str] = mapped_column(String(20))  # vinculado | revisao | quarentena | inativo
    motivo: Mapped[str | None] = mapped_column(String(60))
    atributos: Mapped[list | None] = mapped_column(JSONType)
    atualizado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EscolaMatricula(Base):
    __tablename__ = "escola_matriculas"
    __table_args__ = (UniqueConstraint("alu_id", "mtu_id"), Index("ix_silver_escola_matricula_aluno", "alu_id"), SV)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    alu_id: Mapped[int] = mapped_column(BigInteger)
    mtu_id: Mapped[int] = mapped_column(Integer)
    alc_id: Mapped[int | None] = mapped_column(Integer)
    matricula: Mapped[str | None] = mapped_column(String(50))
    tur_id: Mapped[int | None] = mapped_column(BigInteger)
    turma: Mapped[str | None] = mapped_column(String(60))
    esc_id: Mapped[int | None] = mapped_column(Integer)
    escola: Mapped[str | None] = mapped_column(String(300))
    escola_inep: Mapped[str | None] = mapped_column(String(20))
    uni_id: Mapped[int | None] = mapped_column(Integer)
    unidade: Mapped[str | None] = mapped_column(String(200))
    unidade_situacao: Mapped[int | None] = mapped_column(Integer)
    unidade_excluida: Mapped[bool | None] = mapped_column(Boolean, default=False, server_default="false")
    situacao: Mapped[int | None] = mapped_column(Integer)
    atual: Mapped[bool] = mapped_column(Boolean, default=False)
    excluida: Mapped[bool] = mapped_column(Boolean, default=False)
    data_alteracao: Mapped[datetime | None] = mapped_column(DateTime(timezone=False))


class EscolaResponsavel(Base):
    __tablename__ = "escola_responsaveis"
    __table_args__ = (UniqueConstraint("alu_id", "alr_id"), Index("ix_silver_escola_resp_aluno", "alu_id"), SV)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    alu_id: Mapped[int] = mapped_column(BigInteger)
    alr_id: Mapped[int] = mapped_column(Integer)
    tra_id: Mapped[int | None] = mapped_column(Integer)
    tipo: Mapped[str | None] = mapped_column(String(100))
    pes_id: Mapped[str | None] = mapped_column(String(36))
    nome: Mapped[str | None] = mapped_column(String(300))
    cpf: Mapped[str | None] = mapped_column(String(11))
    principal: Mapped[bool | None] = mapped_column(Boolean)
    situacao: Mapped[int | None] = mapped_column(Integer)
    excluido: Mapped[bool] = mapped_column(Boolean, default=False)
