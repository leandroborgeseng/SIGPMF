"""Prata das empresas da API SIL (por CCM), ligadas ao cidadão pelo CPF. Aditiva: só tabela nova.

Revision ID: 0016
Revises: 0015
"""
import sqlalchemy as sa
from alembic import op

revision = '0016'
down_revision = '0015'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "empresas_sil",
        sa.Column("ccm", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("documento", sa.String(14)), sa.Column("tipo_pessoa", sa.String(2)), sa.Column("cidadao_id", sa.String(36)),
        sa.Column("nome", sa.String(250)), sa.Column("possui_debito", sa.Boolean()), sa.Column("endereco", sa.String(300)),
        sa.Column("bairro", sa.String(120)), sa.Column("cep", sa.String(12)),
        schema="silver")
    op.create_index("ix_silver_empresa_sil_cidadao", "empresas_sil", ["cidadao_id"], schema="silver")
    op.create_index("ix_silver_empresa_sil_doc", "empresas_sil", ["documento"], schema="silver")


def downgrade() -> None:
    op.drop_table("empresas_sil", schema="silver")
