"""Conector SEI: coluna ambiente_origem nas execuções. Aditiva.

Revision ID: 0022
Revises: 0021
"""
from alembic import op

revision = '0022'
down_revision = '0021'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE controle.execucoes ADD COLUMN IF NOT EXISTS ambiente_origem varchar(20)")


def downgrade() -> None:
    """Sem efeito: a coluna ambiente_origem é mantida (não apagar dados de procedência)."""
    pass
