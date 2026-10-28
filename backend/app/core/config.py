"""Configuração estruturada lida do ambiente (nenhum segredo tem valor padrão)."""
from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

VERSION = "0.1.0"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = Field("prod", alias="APP_ENV")  # padrão seguro: demo/sintético só com dev ou test explícito
    database_url: str = Field(..., alias="DATABASE_URL")
    jwt_secret: str = Field(..., alias="JWT_SECRET", min_length=16)
    jwt_expira_minutos: int = Field(60, alias="JWT_EXPIRA_MINUTOS")
    admin_email: str | None = Field(None, alias="ADMIN_EMAIL")
    admin_password: str | None = Field(None, alias="ADMIN_PASSWORD")
    revelar_motivo_min: int = Field(10, alias="REVELAR_MOTIVO_MIN", ge=1)
    revelar_cpf_segundos: int = Field(60, alias="REVELAR_CPF_SEGUNDOS", ge=5)
    docs_habilitado: bool = Field(False, alias="DOCS_HABILITADO")
    cpf_reveal_perfis: str = Field("admin_lgpd", alias="CPF_REVEAL_PERFIS")
    cors_origins: str = Field("", alias="CORS_ORIGINS")
    # Conector SQL Server (somente leitura). Nenhuma origem real nesta entrega.
    sqlserver_host: str | None = Field(None, alias="SQLSERVER_HOST")
    sqlserver_port: int = Field(1433, alias="SQLSERVER_PORT")
    sqlserver_database: str | None = Field(None, alias="SQLSERVER_DATABASE")
    # Banco por conector (cai no SQLSERVER_DATABASE quando vazio; o fallback fica registrado no controle).
    sqlserver_database_saude: str | None = Field(None, alias="SQLSERVER_DATABASE_SAUDE")
    sqlserver_database_cemiterio: str | None = Field(None, alias="SQLSERVER_DATABASE_CEMITERIO")
    sqlserver_database_iss: str | None = Field(None, alias="SQLSERVER_DATABASE_ISS")
    sqlserver_database_iptu: str | None = Field(None, alias="SQLSERVER_DATABASE_IPTU")
    sqlserver_database_divida: str | None = Field(None, alias="SQLSERVER_DATABASE_DIVIDA")
    sqlserver_database_financeiro: str | None = Field(None, alias="SQLSERVER_DATABASE_FINANCEIRO")
    # TR1: Financeiro_Exercicio só a partir deste exercício (vazio = ano anterior ao atual)
    financeiro_exercicio_min: int | None = Field(None, alias="FINANCEIRO_EXERCICIO_MIN", ge=1990, le=2100)
    sqlserver_user: str | None = Field(None, alias="SQLSERVER_USER")
    sqlserver_password: str | None = Field(None, alias="SQLSERVER_PASSWORD")
    sqlserver_timeout: int = Field(5, alias="SQLSERVER_TIMEOUT")  # login (conexão)
    # Tempo máximo de cada consulta. Contagem/MAX de tabelas grandes (ex.: Consultas da Saúde) passa de 5 s.
    sqlserver_consulta_timeout: int = Field(120, alias="SQLSERVER_CONSULTA_TIMEOUT")
    amostra_limite: int = Field(1000, alias="AMOSTRA_LIMITE", ge=1, le=10000)
    # API SIL (tributário novo), só leitura. Produção: https://interface.siltecnologia.com.br/v1
    sil_base_url: str | None = Field(None, alias="SIL_BASE_URL")
    sil_client_id: str | None = Field(None, alias="SIL_CLIENT_ID")
    sil_client_secret: str | None = Field(None, alias="SIL_CLIENT_SECRET")
    sil_scope: str | None = Field(None, alias="SIL_SCOPE")
    sil_grant_type: str = Field("client_credentials", alias="SIL_GRANT_TYPE")
    sil_timeout: int = Field(60, alias="SIL_TIMEOUT", ge=1, le=600)
    sil_intervalo_ms: int = Field(150, alias="SIL_INTERVALO_MS", ge=0, le=10000)  # pausa entre chamadas
    correlacao_limiar: float = Field(0.9, alias="CORRELACAO_LIMIAR", ge=0, le=1)
    lote_tamanho: int = Field(500, alias="LOTE_TAMANHO", ge=1)
    # Carga completa por conector: lote limitado (teto 5000) e pausa entre lotes para não pesar na origem.
    carga_lote: int = Field(1000, alias="CARGA_COMPLETA_LOTE", ge=1, le=5000)
    carga_nulos_limite_pct: float = Field(1.0, alias="SIGPMF_CARGA_NULOS_LIMITE_PCT", ge=0, le=100)
    # CC9: mínimo absoluto de nulos para bloquear (tabelas pequenas: 1 nula em 50 não passa de 1% "de verdade").
    carga_nulos_limite_min: int = Field(10, alias="SIGPMF_CARGA_NULOS_LIMITE_MIN", ge=0)
    carga_alerta_min: int = Field(5, alias="SIGPMF_CARGA_ALERTA_MIN", ge=1, le=1440)
    carga_pausa_ms: int = Field(200, alias="CARGA_COMPLETA_PAUSA_MS", ge=0, le=60000)
    # AG1–AG6: agenda das cargas completas (uma por vez, só dentro da janela). Desligada por padrão.
    agenda_habilitada: bool = Field(False, alias="AGENDA_HABILITADA")
    agenda_janela: str = Field("00:00-06:00", alias="AGENDA_JANELA")  # "00:00-24:00" = o dia todo (homologação)
    agenda_fuso: str = Field("America/Sao_Paulo", alias="AGENDA_FUSO")
    agenda_intervalo_s: int = Field(60, alias="AGENDA_INTERVALO_S", ge=5, le=3600)
    agenda_pular_sem_mudanca: bool = Field(True, alias="AGENDA_PULAR_SEM_MUDANCA")
    agenda_tentativas: int = Field(3, alias="AGENDA_TENTATIVAS", ge=1, le=10)
    agenda_reespera_min: int = Field(30, alias="AGENDA_REESPERA_MIN", ge=1, le=1440)
    agenda_espera_max_h: int = Field(20, alias="AGENDA_ESPERA_MAX_H", ge=1, le=72)
    # IN3: a agenda faz carga incremental e, a cada N dias, uma completa (pega exclusões na origem).
    agenda_completa_dias: int = Field(7, alias="AGENDA_COMPLETA_DIAS", ge=1, le=90)
    # PL2: retenção diária (versões antigas da bronze e logs técnicos; a auditoria nunca é apagada)
    retencao_bronze_versoes_dias: int = Field(7, alias="RETENCAO_BRONZE_VERSOES_DIAS", ge=1, le=3650)
    retencao_logs_dias: int = Field(90, alias="RETENCAO_LOGS_DIAS", ge=7, le=3650)
    # IN2: a incremental relê desde (maior data já gravada na bronze − margem), para não perder alterações tardias.
    carga_incremental_margem_h: int = Field(24, alias="CARGA_INCREMENTAL_MARGEM_H", ge=0, le=720)
    # Alerta de falha por e-mail (opcional: sem SMTP_HOST, só fica registrado na tela e na auditoria).
    smtp_host: str | None = Field(None, alias="SMTP_HOST")
    smtp_port: int = Field(587, alias="SMTP_PORT")
    smtp_usuario: str | None = Field(None, alias="SMTP_USUARIO")
    smtp_senha: str | None = Field(None, alias="SMTP_SENHA")
    smtp_starttls: bool = Field(True, alias="SMTP_STARTTLS")
    alerta_de: str | None = Field(None, alias="ALERTA_DE")
    alerta_para: str = Field("", alias="ALERTA_PARA")  # e-mails separados por vírgula

    @field_validator("financeiro_exercicio_min", mode="before")
    @classmethod
    def _vazio_e_nulo(cls, v):
        """Variável presente e vazia no .env (como no .env.example) = não definida."""
        return None if isinstance(v, str) and not v.strip() else v

    @property
    def perfis_reveal(self) -> set[str]:
        return {p.strip() for p in self.cpf_reveal_perfis.split(",") if p.strip()}


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
