"""Carga completa: etapa atual (leitura/prata/ouro/concluida), contador da etapa e sinal de vida (CC7).

Aditiva: só colunas novas, nulas, em controle.execucoes. Nada é alterado nem apagado.

Revision ID: 0009
Revises: 0008
"""
import sqlalchemy as sa
from alembic import op

revision = '0009'
down_revision = '0008'
branch_labels = None
depends_on = None

COLUNAS = (("etapa", sa.String(20)), ("processadas_etapa", sa.Integer()), ("total_etapa", sa.Integer()),
           ("atualizado_em", sa.DateTime(timezone=True)))


def upgrade() -> None:
    for nome, tipo in COLUNAS:
        op.add_column("execucoes", sa.Column(nome, tipo, nullable=True), schema="controle")


def downgrade() -> None:
    for nome, _ in reversed(COLUNAS):
        op.drop_column("execucoes", nome, schema="controle")
