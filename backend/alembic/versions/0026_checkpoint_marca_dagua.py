"""IN1: marca d'água por tabela em controle.checkpoints_carga (carga incremental da educação).

Aditiva: só cria a coluna `marca_dagua` (timestamp sem fuso, como a origem grava `*_dataAlteracao`), nula.
Nenhuma linha é alterada; sem marca, a incremental usa a maior data já gravada na bronze. Downgrade remove a coluna.

Revision ID: 0026
Revises: 0025 (0025_educacao_trava_completa)
"""
from alembic import op

revision = '0026'
down_revision = '0025'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # IF NOT EXISTS: idempotente (banco re-carimbado numa revisão anterior não falha)
    op.execute("ALTER TABLE controle.checkpoints_carga ADD COLUMN IF NOT EXISTS marca_dagua timestamp without time zone")


def downgrade() -> None:
    op.execute("ALTER TABLE controle.checkpoints_carga DROP COLUMN IF EXISTS marca_dagua")
