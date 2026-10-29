"""Bronze: ambiente_origem (producao|sandbox) por linha. Aditiva: NOT NULL DEFAULT 'producao' (todas as linhas atuais
ficam 'producao'). A prata da SIL só lê 'producao'.

Revision ID: 0019
Revises: 0018
"""
import sqlalchemy as sa
from alembic import op

revision = '0019'
down_revision = '0018'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("registros", sa.Column("ambiente_origem", sa.String(20), nullable=False,
                                         server_default="producao"), schema="bronze")


def downgrade() -> None:
    op.drop_column("registros", "ambiente_origem", schema="bronze")
