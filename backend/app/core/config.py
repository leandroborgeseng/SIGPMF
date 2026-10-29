"""Configuração estruturada lida do ambiente (nenhum segredo tem valor padrão)."""
from functools import lru_cache

from pydantic import Field, SecretStr, field_validator
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
    # N3a: números de preenchimento de CPF (vírgula). Dígitos todos iguais já são regra própria.
    cpf_preenchimento: str = Field("12345678909", alias="CPF_PREENCHIMENTO")
    # PR-03 (R1 do QA #48): segredo (>= 32 caracteres, checado no create_app sem ecoar o valor) do HMAC-SHA256 das chaves da prata do modelo. Sem padrão; nunca vai para log.
    chave_hash_segredo: SecretStr | None = Field(None, alias="SIGPMF_CHAVE_HASH_SEGREDO", repr=False)
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
    # PR-07 (J1): login de permissão mínima (só SELECT) exclusivo do job de contagens; nunca cai no login geral.
    sqlserver_contagens_user: str | None = Field(None, alias="SQLSERVER_CONTAGENS_USER")
    sqlserver_contagens_password: str | None = Field(None, alias="SQLSERVER_CONTAGENS_PASSWORD")
    # J4: só CPFs com pelo menos este número de repetições entram na lista (configurável; aprovado: 50)
    # J4a: piso 50 travado no código (job.limiar_efetivo): valor menor registra erro e usa 50
    contagens_cpf_repetido_limiar: int = Field(50, alias="CONTAGENS_CPF_REPETIDO_LIMIAR", le=1000000)
    contagens_cpf_repetido_top: int = Field(100, alias="CONTAGENS_CPF_REPETIDO_TOP", ge=1, le=1000)
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
    # Conector de educação (FRA_GestaoEscolar + FRA_CoreSSO), só leitura. Credencial só por variável de ambiente.
    ge_host: str | None = Field(None, alias="SIGPMF_SRC_GESTAOESCOLAR_HOST")  # vazio = SQLSERVER_HOST
    ge_port: int | None = Field(None, alias="SIGPMF_SRC_GESTAOESCOLAR_PORT")  # vazio = SQLSERVER_PORT
    ge_user: str | None = Field(None, alias="SIGPMF_SRC_GESTAOESCOLAR_USER")
    ge_password: str | None = Field(None, alias="SIGPMF_SRC_GESTAOESCOLAR_PASSWORD")
    ge_banco_escolar: str = Field("FRA_GestaoEscolar", alias="SIGPMF_SRC_GESTAOESCOLAR_DB")
    ge_banco_pessoas: str = Field("FRA_CoreSSO", alias="SIGPMF_SRC_CORESSO_DB")
    ge_lote: int = Field(1000, alias="SIGPMF_GESTAOESCOLAR_LOTE", ge=1, le=5000)
    ge_max_lotes: int = Field(1, alias="SIGPMF_GESTAOESCOLAR_MAX_LOTES", ge=1, le=10000)  # teto por execução
    ge_amostra: int = Field(50, alias="SIGPMF_GESTAOESCOLAR_AMOSTRA", ge=1, le=50)
    # Carga completa da educação só na janela noturna (AGENDA_JANELA). Só "false" (qualquer caixa) desliga a trava;
    # ausente, vazio ou qualquer outro valor = ligada. Texto (não bool) para valor inválido não derrubar a app nem desligar.
    ge_janela_noturna: str | None = Field(None, alias="SIGPMF_EDUCACAO_JANELA_NOTURNA")
    # Execução da carga completa da educação sem batida (atualizado_em) há mais que N min pode ser marcada falha pelo ADMIN.
    ge_orfa_min: int = Field(30, alias="SIGPMF_EDUCACAO_ORFA_MIN", ge=1, le=1440)
    # Pausa entre lotes da carga completa da educação (ms). Texto: vazio/inválido/negativo = 0 (com WARNING).
    ge_pausa_lote_ms: str | None = Field(None, alias="SIGPMF_GESTAOESCOLAR_PAUSA_LOTE_MS")
    # Releitura de UMA fonte da educação no boot (uso único; ver docs/OPERATIONS.md). Vazio = nada acontece.
    releitura_fonte: str = Field("", alias="SIGPMF_RELEITURA_FONTE")
    # Espera máxima (s) pelo /health da própria app antes de começar; passou disso = recusa `sem_saude`.
    releitura_saude_max_s: int = Field(600, alias="SIGPMF_RELEITURA_SAUDE_MAX_S", ge=1, le=3600)
    releitura_saude_porta: int = Field(8000, alias="SIGPMF_RELEITURA_SAUDE_PORTA", ge=1, le=65535)
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
    # IN6/R3: a primeira execução agendada (sem ultima_completa_em) é sempre completa. Depois: 0 (padrão) = só
    # incremental (outras completas só por ADMIN, manual e auditada); N > 0 = completa a cada N dias.
    agenda_completa_dias: int = Field(0, alias="AGENDA_COMPLETA_DIAS", ge=0, le=90)
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

    @field_validator("financeiro_exercicio_min", "ge_port", mode="before")
    @classmethod
    def _vazio_e_nulo(cls, v):
        """Variável presente e vazia no .env (como no .env.example) = não definida."""
        return None if isinstance(v, str) and not v.strip() else v

    def segredo_chave_hash_ok(self) -> str | None:
        """Valor do segredo do HMAC, ou None se ausente ou com menos de 32 caracteres úteis (espaços/tabs não contam)."""
        v = self.chave_hash_segredo.get_secret_value() if self.chave_hash_segredo else ""
        return v if len((v or "").strip()) >= 32 else None

    @property
    def perfis_reveal(self) -> set[str]:
        return {p.strip() for p in self.cpf_reveal_perfis.split(",") if p.strip()}


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
