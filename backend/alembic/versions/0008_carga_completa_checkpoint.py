"""Carga completa por conector: checkpoint por tabela e progresso/cancelamento na execução.

Aditiva: tabela nova (controle.checkpoints_carga) e colunas novas, nulas ou com padrão, em controle.execucoes.

Revision ID: 0008
Revises: 0007
"""
import sqlalchemy as sa
from alembic import op

revision = '0008'
down_revision = '0007'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("execucoes", sa.Column("total_origem", sa.Integer(), nullable=True), schema="controle")
    op.add_column("execucoes", sa.Column("tabela_atual", sa.String(200), nullable=True), schema="controle")
    op.add_column("execucoes", sa.Column("pausa_motivo", sa.Text(), nullable=True), schema="controle")
    op.add_column("execucoes", sa.Column("cancelar", sa.Boolean(), nullable=False, server_default=sa.false()),
                  schema="controle")
    op.create_table(
        "checkpoints_carga",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("sistema", sa.String(50), nullable=False),
        sa.Column("tabela", sa.String(200), nullable=False),
        sa.Column("posicao", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_origem", sa.Integer(), nullable=True),
        sa.Column("concluida", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("execucao_id", sa.Integer(), sa.ForeignKey("controle.execucoes.id"), nullable=True),
        sa.Column("atualizado_em", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("sistema", "tabela", name="uq_checkpoint_sistema_tabela"),
        schema="controle",
    )


def downgrade() -> None:
    op.drop_table("checkpoints_carga", schema="controle")
    for c in ("cancelar", "pausa_motivo", "tabela_atual", "total_origem"):
        op.drop_column("execucoes", c, schema="controle")
