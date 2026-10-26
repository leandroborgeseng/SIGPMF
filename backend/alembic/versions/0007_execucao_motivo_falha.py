"""controle.execucoes: motivo seguro da falha (classe, número SQL Server, tabela, objeto, local)

Aditiva: só colunas novas, nulas.

Revision ID: 0007
Revises: 0006
"""
import sqlalchemy as sa
from alembic import op

revision = '0007'
down_revision = '0006'
branch_labels = None
depends_on = None

_COLS = (("erro_classe", sa.String(80)), ("erro_numero", sa.Integer()), ("erro_tabela", sa.String(200)),
         ("erro_objeto", sa.String(200)), ("erro_local", sa.String(200)))


def upgrade() -> None:
    for nome, tipo in _COLS:
        op.add_column("execucoes", sa.Column(nome, tipo, nullable=True), schema="controle")


def downgrade() -> None:
    for nome, _ in reversed(_COLS):
        op.drop_column("execucoes", nome, schema="controle")
