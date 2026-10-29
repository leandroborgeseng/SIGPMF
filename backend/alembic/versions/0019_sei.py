"""Conector SEI: coluna ambiente_origem nas execuções. Aditiva.

Revision ID: 0019
Revises: 0018
"""
from alembic import op

revision = '0019'
down_revision = '0018'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE controle.execucoes ADD COLUMN IF NOT EXISTS ambiente_origem varchar(20)")


def downgrade() -> None:
    op.execute("ALTER TABLE controle.execucoes DROP COLUMN IF EXISTS ambiente_origem")
