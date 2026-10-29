"""Educação: unidade escolar na matrícula (uni_id, uni_descricao, uni_situacao). Aditiva: só colunas novas anuláveis.

Revision ID: 0018
Revises: 0017
"""
import sqlalchemy as sa
from alembic import op

revision = '0018'
down_revision = '0017'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("escola_matriculas", sa.Column("uni_id", sa.Integer(), nullable=True), schema="silver")
    op.add_column("escola_matriculas", sa.Column("unidade", sa.String(200), nullable=True), schema="silver")
    op.add_column("escola_matriculas", sa.Column("unidade_situacao", sa.Integer(), nullable=True), schema="silver")
    op.add_column("escola_matriculas", sa.Column("unidade_excluida", sa.Boolean(), nullable=True,
                                                 server_default=sa.false()), schema="silver")


def downgrade() -> None:
    for c in ("unidade_excluida", "unidade_situacao", "unidade", "uni_id"):
        op.drop_column("escola_matriculas", c, schema="silver")
