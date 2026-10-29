"""Prata da educação (FRA_GestaoEscolar + FRA_CoreSSO). Aditiva: só tabelas novas.

Revision ID: 0017
Revises: 0016
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = '0017'
down_revision = '0016'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "escola_alunos",
        sa.Column("alu_id", sa.BigInteger(), primary_key=True, autoincrement=False),
        sa.Column("pes_id", sa.String(36)), sa.Column("situacao", sa.Integer()),
        sa.Column("excluido", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("nome", sa.String(300)), sa.Column("nome_social", sa.String(300)), sa.Column("data_nascimento", sa.Date()),
        sa.Column("nome_mae", sa.String(300)), sa.Column("cpf", sa.String(11)), sa.Column("nis", sa.String(11)),
        sa.Column("certidao", sa.String(40)), sa.Column("cep", sa.String(10)), sa.Column("logradouro", sa.String(300)),
        sa.Column("numero", sa.String(30)), sa.Column("complemento", sa.String(200)), sa.Column("bairro", sa.String(200)),
        sa.Column("cidadao_id", sa.String(36)), sa.Column("confianca", sa.Float()),
        sa.Column("status_vinculo", sa.String(20), nullable=False), sa.Column("motivo", sa.String(60)),
        sa.Column("atributos", postgresql.JSONB()), sa.Column("atualizado_em", sa.DateTime(timezone=True)),
        schema="silver")
    op.create_index("ix_silver_escola_aluno_cidadao", "escola_alunos", ["cidadao_id"], schema="silver")
    op.create_table(
        "escola_matriculas",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("alu_id", sa.BigInteger(), nullable=False), sa.Column("mtu_id", sa.Integer(), nullable=False),
        sa.Column("alc_id", sa.Integer()), sa.Column("matricula", sa.String(50)), sa.Column("tur_id", sa.BigInteger()),
        sa.Column("turma", sa.String(60)), sa.Column("esc_id", sa.Integer()), sa.Column("escola", sa.String(300)),
        sa.Column("escola_inep", sa.String(20)), sa.Column("situacao", sa.Integer()),
        sa.Column("atual", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("excluida", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("data_alteracao", sa.DateTime()),
        sa.UniqueConstraint("alu_id", "mtu_id"), schema="silver")
    op.create_index("ix_silver_escola_matricula_aluno", "escola_matriculas", ["alu_id"], schema="silver")
    op.create_table(
        "escola_responsaveis",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("alu_id", sa.BigInteger(), nullable=False), sa.Column("alr_id", sa.Integer(), nullable=False),
        sa.Column("tra_id", sa.Integer()), sa.Column("tipo", sa.String(100)), sa.Column("pes_id", sa.String(36)),
        sa.Column("nome", sa.String(300)), sa.Column("cpf", sa.String(11)), sa.Column("principal", sa.Boolean()),
        sa.Column("situacao", sa.Integer()),
        sa.Column("excluido", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.UniqueConstraint("alu_id", "alr_id"), schema="silver")
    op.create_index("ix_silver_escola_resp_aluno", "escola_responsaveis", ["alu_id"], schema="silver")


def downgrade() -> None:
    for t in ("escola_responsaveis", "escola_matriculas", "escola_alunos"):
        op.drop_table(t, schema="silver")
