"""Dívida em aberto (DP-P20/DP-P4/DP-P19): colunas aditivas em silver.dividas para o saldo líquido por linha do débito.

- `compo` (situação COMPO da origem), `sequ`, `saldo_bruto` (VR_TOTA_DIVI), `pago_conta_corrente` (soma de Vr_Pago da
  Divida_Conta_Corrente por CODI/SEQU/ANO), `em_aberto` (COMPO D/G/P/B, não extinta, saldo > 0) e `qualidade`.
- `saldo` passa a ser o líquido (piso 0); a prata é reconstruída (DELETE + INSERT) na próxima carga.

Aditiva e idempotente: ADD COLUMN IF NOT EXISTS. Downgrade não apaga nada (convenção 0044/0045).

Revision ID: 0046
Revises: 0045
"""
from alembic import op

revision = '0046'
down_revision = '0045'
branch_labels = None
depends_on = None

COLUNAS = (("sequ", "VARCHAR(4)"), ("compo", "VARCHAR(1)"), ("saldo_bruto", "NUMERIC(14, 2)"),
           ("pago_conta_corrente", "NUMERIC(14, 2)"), ("em_aberto", "BOOLEAN"), ("qualidade", "VARCHAR(30)"))


def upgrade() -> None:
    for nome, tipo in COLUNAS:
        op.execute(f"ALTER TABLE silver.dividas ADD COLUMN IF NOT EXISTS {nome} {tipo}")
    op.execute("CREATE INDEX IF NOT EXISTS ix_silver_divida_aberto ON silver.dividas (em_aberto)")


def downgrade() -> None:
    # Aditiva: o downgrade não remove colunas nem dados da prata.
    pass
