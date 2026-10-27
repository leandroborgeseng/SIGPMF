"""Carga incremental e reconstrução de prata/ouro só quando as entradas mudam (IN1–IN4).

Aditiva:
- controle.execucoes.modo ('completa' | 'incremental'; nulo nas execuções antigas = completa);
- controle.agendamentos.ultima_completa_em (a agenda faz uma completa a cada AGENDA_COMPLETA_DIAS);
- controle.camadas_estado (uma linha): assinatura das entradas da última reconstrução bem-sucedida de prata e ouro.

Revision ID: 0012
Revises: 0011
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = '0012'
down_revision = '0011'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("execucoes", sa.Column("modo", sa.String(20), nullable=True), schema="controle")
    op.add_column("agendamentos", sa.Column("ultima_completa_em", sa.DateTime(timezone=True), nullable=True),
                  schema="controle")
    op.create_table(
        "camadas_estado",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("assinatura", JSONB(), nullable=False),
        sa.Column("execucao_id", sa.Integer(), sa.ForeignKey("controle.execucoes.id"), nullable=True),
        sa.Column("reconstruido_em", sa.DateTime(timezone=True), server_default=sa.func.now()),
        schema="controle",
    )


def downgrade() -> None:
    op.drop_table("camadas_estado", schema="controle")
    op.drop_column("agendamentos", "ultima_completa_em", schema="controle")
    op.drop_column("execucoes", "modo", schema="controle")
