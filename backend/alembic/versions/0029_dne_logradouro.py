"""PR-02: referência DNE (Correios) por CEP. Aditiva e idempotente: só cria o schema `ref` e a tabela nova.

`ref.dne_logradouro` tem o CEP (8 dígitos) como chave única; o `Codigo_Logradouro` da PMF nunca é chave (D6).
Downgrade remove só esta tabela nova.

Revision ID: 0029
Revises: 0026  (o #44 reserva a 0027 e o PR-01 não precisou da 0028; ajustar para a ponta da main no merge)
"""
import sqlalchemy as sa
from alembic import op

revision = '0029'
down_revision = '0026'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS ref")
    if sa.inspect(op.get_bind()).has_table("dne_logradouro", schema="ref"):
        return  # idempotente
    op.create_table(
        "dne_logradouro",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("cep", sa.CHAR(8), nullable=False),
        sa.Column("tipo_logradouro", sa.String(80)),
        sa.Column("logradouro", sa.String(300), nullable=False),
        sa.Column("bairro", sa.String(200)),
        sa.Column("localidade", sa.String(200)),
        sa.Column("uf", sa.CHAR(2)),
        sa.Column("dne_id", sa.String(20)),  # LOG_NU do DNE: só procedência, não é chave
        sa.Column("hash", sa.String(64), nullable=False),
        sa.Column("execucao_id", sa.Integer),
        sa.Column("criado_em", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("atualizado_em", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("cep", name="uq_ref_dne_logradouro_cep"),
        schema="ref",
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS ref.dne_logradouro")
