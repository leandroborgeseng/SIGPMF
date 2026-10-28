"""Saúde (fatos), linha do tempo e ouro agregado (onda 3, SA-F1..SA-F6). Aditiva: só tabelas novas.

Revision ID: 0014
Revises: 0013
"""
import sqlalchemy as sa
from alembic import op

revision = '0014'
down_revision = '0013'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("unidades_saude", sa.Column("codigo", sa.Integer(), primary_key=True), sa.Column("nome", sa.String(250)),
                    sa.Column("tipo", sa.String(5)), sa.Column("ativa", sa.Boolean(), nullable=False),
                    sa.Column("cd_bairro", sa.Integer()), schema="silver")
    op.create_table("atendimentos", sa.Column("id", sa.Integer(), primary_key=True),
                    sa.Column("chave_origem", sa.String(200), nullable=False, unique=True), sa.Column("matricula", sa.String(30)),
                    sa.Column("cidadao_id", sa.String(36)), sa.Column("data", sa.Date()), sa.Column("unidade", sa.Integer()),
                    sa.Column("especialidade", sa.Integer()), schema="silver")
    op.create_index("ix_silver_atendimento_cidadao", "atendimentos", ["cidadao_id"], schema="silver")
    op.create_index("ix_silver_atendimento_data", "atendimentos", ["data"], schema="silver")
    op.create_table("agendamentos", sa.Column("id", sa.Integer(), primary_key=True),
                    sa.Column("chave_origem", sa.String(200), nullable=False, unique=True), sa.Column("matricula", sa.String(30)),
                    sa.Column("cidadao_id", sa.String(36)), sa.Column("data_consulta", sa.Date()), sa.Column("data_marcacao", sa.Date()),
                    sa.Column("dias_espera", sa.Integer()), sa.Column("unidade", sa.Integer()), sa.Column("especialidade", sa.Integer()),
                    schema="silver")
    op.create_index("ix_silver_agendamento_cidadao", "agendamentos", ["cidadao_id"], schema="silver")
    op.create_index("ix_silver_agendamento_data", "agendamentos", ["data_consulta"], schema="silver")
    op.create_table("vacinacoes", sa.Column("id", sa.Integer(), primary_key=True),
                    sa.Column("chave_origem", sa.String(200), nullable=False, unique=True), sa.Column("matricula", sa.String(30)),
                    sa.Column("cidadao_id", sa.String(36)), sa.Column("data", sa.Date()), sa.Column("unidade", sa.Integer()),
                    sa.Column("vacina", sa.String(60)), sa.Column("dose", sa.String(10)), schema="silver")
    op.create_index("ix_silver_vacinacao_cidadao", "vacinacoes", ["cidadao_id"], schema="silver")
    op.create_index("ix_silver_vacinacao_data", "vacinacoes", ["data"], schema="silver")
    op.create_table("eventos", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("cidadao_id", sa.String(36), nullable=False),
                    sa.Column("data", sa.Date(), nullable=False), sa.Column("area", sa.String(20), nullable=False),
                    sa.Column("tipo", sa.String(40), nullable=False), sa.Column("titulo", sa.String(300), nullable=False),
                    sa.Column("fonte", sa.String(50), nullable=False), schema="silver")
    op.create_index("ix_silver_evento_cidadao_data", "eventos", ["cidadao_id", "data"], schema="silver")
    op.create_table("indicadores", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("serie", sa.String(60), nullable=False),
                    sa.Column("periodo", sa.String(7), nullable=False, server_default=""),
                    sa.Column("dim", sa.String(200), nullable=False, server_default=""), sa.Column("valor", sa.Float(), nullable=False),
                    sa.Column("atualizado_em", sa.DateTime(timezone=True), server_default=sa.func.now()), schema="gold")
    op.create_index("ix_gold_indicador_serie", "indicadores", ["serie", "periodo"], schema="gold")


def downgrade() -> None:
    op.drop_table("indicadores", schema="gold")
    for t in ("eventos", "vacinacoes", "agendamentos", "atendimentos", "unidades_saude"):
        op.drop_table(t, schema="silver")
