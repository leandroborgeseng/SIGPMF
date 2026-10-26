from pydantic import BaseModel, Field, model_validator


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
