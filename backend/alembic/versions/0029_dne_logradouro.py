"""PR-02: referência DNE (Correios) por CEP. Aditiva e idempotente: só cria o schema `ref` e a tabela nova.

`ref.dne_logradouro` tem o CEP (8 dígitos) como chave única; o `Codigo_Logradouro` da PMF nunca é chave (D6).
Downgrade remove só esta tabela nova.

Revision ID: 0029
Revises: 0027  (#44 na main; a 0028 reservada ao PR-01 não foi usada)
"""
import sqlalchemy as sa
from alembic import op

revision = '0029'
down_revision = '0027'
branch_labels = None
depends_on = None

# Fonte: Correios.dbo.LOG_LOGRADOURO_SP + LOG_BAIRRO (bairro inicial) + LOG_LOCALIDADE. Id_Logradouro só procedência.
COLUNAS = [
    ("tipo_logradouro", sa.String(80)),
    ("logradouro", sa.String(300)),
    ("logradouro_abreviado", sa.String(300)),
    ("complemento", sa.String(300)),
    ("bairro", sa.String(200)),
    ("localidade", sa.String(200)),
    ("codigo_ibge", sa.Integer()),
    ("uf", sa.CHAR(2)),
    ("id_logradouro", sa.Integer()),
    ("hash", sa.String(64)),
    ("execucao_id", sa.Integer()),
]


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS ref")
    if sa.inspect(op.get_bind()).has_table("dne_logradouro", schema="ref"):
        for n, t in COLUNAS:  # idempotente e aditivo: só acrescenta o que faltar
            op.execute(f"ALTER TABLE ref.dne_logradouro ADD COLUMN IF NOT EXISTS {n} {t.compile(dialect=op.get_bind().dialect)}")
        return
    op.create_table(
        "dne_logradouro",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("cep", sa.CHAR(8), nullable=False),
        *[sa.Column(n, t) for n, t in COLUNAS],
        sa.Column("criado_em", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("atualizado_em", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("cep", name="uq_ref_dne_logradouro_cep"),
        schema="ref",
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS ref.dne_logradouro")
