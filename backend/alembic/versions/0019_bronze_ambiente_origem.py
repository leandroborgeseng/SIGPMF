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
    # Idempotente: após um downgrade (no-op) a coluna já existe; reaplicar não pode falhar nem tocar nas marcas.
    insp = sa.inspect(op.get_bind())
    if any(c["name"] == "ambiente_origem" for c in insp.get_columns("registros", schema="bronze")):
        return
    op.add_column("registros", sa.Column("ambiente_origem", sa.String(20), nullable=False,
                                         server_default="producao"), schema="bronze")


def downgrade() -> None:
    # No-op de propósito: apagar a coluna perderia em silêncio as marcas 'sandbox' (execuções 25/31 da 0020).
    # Downgrade nunca apaga dado. Na 0018 a coluna fica lá, ignorada (tem DEFAULT, inserts antigos seguem válidos).
    pass
