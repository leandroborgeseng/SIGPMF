"""IEG-M (TCE-SP): silver.iegm_faixa, a faixa de cada índice por ano, do município e do estado. Aditiva e idempotente.

Revision ID: 0039
Revises: 0038
"""
import sqlalchemy as sa
from alembic import op

revision = '0039'
down_revision = '0038'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "iegm_faixa",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ano_base", sa.Integer(), nullable=False), sa.Column("ano_apuracao", sa.Integer(), nullable=False),
        sa.Column("indice", sa.String(12), nullable=False), sa.Column("abrangencia", sa.String(10), nullable=False),
        sa.Column("faixa", sa.String(2), nullable=False), sa.Column("municipios", sa.Integer(), nullable=False),
        sa.Column("execucao_id", sa.Integer()),
        sa.Column("carregado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("ano_base", "indice", "abrangencia", "faixa", name="uq_iegm_faixa"),
        schema="silver", if_not_exists=True)


def downgrade() -> None:
    pass  # sem exclusão física (padrão do projeto)
