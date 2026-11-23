"""Plano de Governo 2025–2028: agregado versionado no banco do SIGPMF.

Aditiva. A carga dos 70 compromissos é referência do aplicativo; a primeira gravação
persiste o agregado sem marcar nenhum compromisso automaticamente como cumprido.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0049"
down_revision = "0048"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "plano_governo_estado",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("versao", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("conteudo", postgresql.JSONB(), nullable=False),
        sa.Column("atualizado_em", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        schema="controle",
    )


def downgrade() -> None:
    op.drop_table("plano_governo_estado", schema="controle")
