"""CC9 no Munícipe: colunas de quarentena na bronze (aditiva e idempotente).

Mesmos nomes do CC9 genérico do factory/sigpmf-etapas-prata (bronze.registros.quarentena / quarentena_campos).
ADD COLUMN IF NOT EXISTS: a 0009 do #14 (etapas-prata) já cria as colunas, então aqui nada muda.
Revisão encadeada (0008 -> 0009 -> 0009m) em vez de merge: um merge deixava o `downgrade -1` ambíguo. Nada é apagado.

Revision ID: 0009m
Revises: 0009
"""
from alembic import op

revision = '0009m'
down_revision = '0009'  # linear depois do #14: a 0009 cria as colunas; esta vira no-op
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE bronze.registros ADD COLUMN IF NOT EXISTS quarentena VARCHAR(40)")
    op.execute("ALTER TABLE bronze.registros ADD COLUMN IF NOT EXISTS quarentena_campos JSONB")


def downgrade() -> None:
    pass  # aditiva: as colunas podem ser do CC9 genérico; não são removidas aqui
