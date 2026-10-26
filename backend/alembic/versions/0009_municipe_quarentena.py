"""CC9 no Munícipe: colunas de quarentena na bronze (aditiva e idempotente).

Mesmos nomes do CC9 genérico do factory/sigpmf-etapas-prata (bronze.registros.quarentena / quarentena_campos).
ADD COLUMN IF NOT EXISTS: se a 0009 do etapas-prata entrar antes, esta revisão não altera nada. Nada é apagado.

Revision ID: 0009m
Revises: 0008
"""
from alembic import op

revision = '0009m'
down_revision = '0008'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE bronze.registros ADD COLUMN IF NOT EXISTS quarentena VARCHAR(40)")
    op.execute("ALTER TABLE bronze.registros ADD COLUMN IF NOT EXISTS quarentena_campos JSONB")


def downgrade() -> None:
    pass  # aditiva: as colunas podem ser do CC9 genérico; não são removidas aqui
