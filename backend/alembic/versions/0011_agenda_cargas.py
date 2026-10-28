"""Agenda das cargas completas (AG1): um horário por conector, executados um por vez dentro da janela.

Aditiva: tabela nova controle.agendamentos, semeada com Saúde (01:00) e Cemitério (03:00), ambos DESLIGADOS
(o admin liga pela tela; a agenda inteira também depende de AGENDA_HABILITADA=true).

Revision ID: 0011
Revises: 0010
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = '0011'
down_revision = '0010'
branch_labels = None
depends_on = None


def upgrade() -> None:
    t = op.create_table(
        "agendamentos",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("conector", sa.String(50), nullable=False, unique=True),
        sa.Column("horario", sa.String(5), nullable=False),  # HH:MM no fuso da agenda
        sa.Column("ativo", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("ordem", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("forcar", sa.Boolean(), nullable=False, server_default=sa.false()),  # "executar agora"
        sa.Column("ultima_data", sa.Date(), nullable=True),  # dia (fuso da agenda) já resolvido
        sa.Column("tentativas", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("proxima_tentativa_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ultimo_status", sa.String(30), nullable=True),
        sa.Column("ultima_mensagem", sa.Text(), nullable=True),
        sa.Column("ultima_execucao_id", sa.Integer(), sa.ForeignKey("controle.execucoes.id"), nullable=True),
        sa.Column("ultimo_inicio_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ultimo_fim_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("assinatura_origem", JSONB(), nullable=True),  # {tabela: [linhas, max(campo_incremental)]}
        sa.Column("atualizado_por", sa.String(200), nullable=True),
        sa.Column("atualizado_em", sa.DateTime(timezone=True), server_default=sa.func.now()),
        schema="controle",
    )
    op.bulk_insert(t, [
        {"conector": "saude", "horario": "01:00", "ativo": False, "ordem": 1},
        {"conector": "cemiterio", "horario": "03:00", "ativo": False, "ordem": 2},
    ])


def downgrade() -> None:
    op.drop_table("agendamentos", schema="controle")
