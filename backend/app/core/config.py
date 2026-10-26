"""Configuração estruturada lida do ambiente (nenhum segredo tem valor padrão)."""
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

VERSION = "0.1.0"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = Field("prod", alias="APP_ENV")  # ausente = produção
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
    sqlserver_user: str | None = Field(None, alias="SQLSERVER_USER")
    sqlserver_password: str | None = Field(None, alias="SQLSERVER_PASSWORD")
    sqlserver_timeout: int = Field(5, alias="SQLSERVER_TIMEOUT")
    amostra_limite: int = Field(1000, alias="AMOSTRA_LIMITE", ge=1, le=10000)
    correlacao_limiar: float = Field(0.9, alias="CORRELACAO_LIMIAR", ge=0, le=1)
    lote_tamanho: int = Field(500, alias="LOTE_TAMANHO", ge=1)

    @property
    def perfis_reveal(self) -> set[str]:
        return {p.strip() for p in self.cpf_reveal_perfis.split(",") if p.strip()}


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
