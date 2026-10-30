from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator


class LoginIn(BaseModel):
    usuario: str | None = None
    email: str | None = None
    senha: str

    @model_validator(mode="after")
    def _id(self):
        if not (self.usuario or self.email):
            raise ValueError("Informe usuario")
        return self

    @property
    def login(self) -> str:
        return (self.usuario or self.email or "").strip().lower()


class SistemaIn(BaseModel):
    codigo: str = Field(min_length=1, max_length=50, pattern=r"^[A-Za-z0-9_]+$")
    nome: str = Field(min_length=1, max_length=200)
    descricao: str | None = None
    dominio: str | None = Field(None, max_length=50)
    ativo: bool = True


class TabelaIn(BaseModel):
    nome: str = Field(min_length=1, max_length=200)
    chave: str = Field(min_length=1, max_length=200)
    campo_incremental: str | None = Field(None, max_length=200)


class UsuarioIn(BaseModel):
    email: str = Field(min_length=3, max_length=200)
    nome: str = Field(min_length=1, max_length=200)
    perfil: str = Field(pattern=r"^(admin|admin_lgpd|operador|leitura)$")
    senha: str = Field(min_length=10)


class RevelarIn(BaseModel):
    motivo: str | None = None


class CargaIn(BaseModel):
    completa: bool = False
    conjunto: str = Field("base", pattern=r"^(base|saude|cemiterio|saude\+cemiterio)$")


class ConfirmaObitoIn(BaseModel):
    cidadao_id: str = Field(min_length=1, max_length=36)
    motivo: str | None = None


class ExecutarBaseIn(BaseModel):
    modo: str = Field("amostra", pattern=r"^(amostra|completa|reprocessar)$")


class InterromperIn(BaseModel):
    """CC12: motivo obrigatório (auditado). Sem dado pessoal."""
    motivo: str = Field(min_length=5, max_length=300)


class AgendaIn(BaseModel):
    """AG1: alteração de um agendamento (admin, auditada). Campos ausentes ficam como estão.
    D1 (#58): campo desconhecido -> 422 (extra=forbid); forcar só aceita bool de verdade (StrictBool: "true"/1 -> 422)."""
    model_config = ConfigDict(extra="forbid")
    horario: str | None = Field(None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    ativo: StrictBool | None = None  # #61 R1: "yes"/"true"/1/0 -> 422
    ordem: int | None = Field(None, ge=0, le=100)
    # Só false: limpa um "Executar agora" pendente (auditado). true continua só pelo POST executar-agora.
    forcar: StrictBool | None = None

    @model_validator(mode="after")
    def _forcar_so_false(self):
        if self.forcar is True and self.ativo is not True:  # FA2: true só junto com ativo=true (religar marcado)
            raise ValueError("forcar só aceita false (limpar execução pendente); use \"Executar agora\" para forçar.")
        return self
