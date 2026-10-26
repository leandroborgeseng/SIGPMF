"""Carga completa: quarentena de chave nula na bronze (CC9) e etapa atual (leitura/prata/ouro/concluida), contador da etapa e sinal de vida (CC7).

Aditiva: só colunas novas, nulas, em controle.execucoes e bronze.registros. Nada é alterado nem apagado.

Revision ID: 0009
Revises: 0008
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = '0009'
down_revision = '0008'
branch_labels = None
depends_on = None

COLUNAS = (("etapa", sa.String(20)), ("processadas_etapa", sa.Integer()), ("total_etapa", sa.Integer()),
           ("atualizado_em", sa.DateTime(timezone=True)))


def upgrade() -> None:
    for nome, tipo in COLUNAS:
        op.add_column("execucoes", sa.Column(nome, tipo, nullable=True), schema="controle")
    op.add_column("registros", sa.Column("quarentena", sa.String(40), nullable=True), schema="bronze")
    op.add_column("registros", sa.Column("quarentena_campos", JSONB(), nullable=True), schema="bronze")


def downgrade() -> None:
    op.drop_column("registros", "quarentena_campos", schema="bronze")
    op.drop_column("registros", "quarentena", schema="bronze")
    for nome, _ in reversed(COLUNAS):
        op.drop_column("execucoes", nome, schema="controle")
