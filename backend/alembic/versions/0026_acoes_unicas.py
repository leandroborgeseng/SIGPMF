"""Ações de uso único no boot (ex.: releitura de uma fonte da educação no deploy). Aditiva: só cria a tabela.

Marcador de idempotência por (acao, fonte, token); na releitura do deploy o token é o SHA do deploy: status `concluida` impede repetir no próximo boot; qualquer outro
status (em_andamento, falha, parcial, recusada) permite tentar de novo. Downgrade remove só esta tabela nova.

Revision ID: 0026
Revises: 0025
"""
import sqlalchemy as sa
from alembic import op

revision = '0026'
down_revision = '0025'
branch_labels = None
depends_on = None


def upgrade() -> None:
    if sa.inspect(op.get_bind()).has_table("acoes_unicas", schema="controle"):
        return  # idempotente (testes reaplicam a partir de um stamp anterior)
    op.create_table(
        "acoes_unicas",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("acao", sa.String(50), nullable=False),
        sa.Column("fonte", sa.String(200), nullable=False),
        sa.Column("token", sa.String(100), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("codigo", sa.String(50)),
        sa.Column("execucao_id", sa.Integer),
        sa.Column("usuario", sa.String(200)),
        sa.Column("tentativas", sa.Integer, nullable=False, server_default="0"),
        sa.Column("criado_em", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("atualizado_em", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("acao", "fonte", "token", name="uq_acoes_unicas_acao_fonte_token"),
        schema="controle",
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS controle.acoes_unicas")
