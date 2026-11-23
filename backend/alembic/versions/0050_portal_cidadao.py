"""Sessões e desafios do portal público, separados dos usuários internos.

Aditiva e idempotente: reaplicar a partir de uma revisão anterior não recria a tabela.
"""
import sqlalchemy as sa
from alembic import op

revision = "0050"
down_revision = "0049"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("portal_acessos", sa.Column("chave", sa.String(64), primary_key=True),
                    sa.Column("tipo", sa.String(16), nullable=False), sa.Column("cidadao_id", sa.String(36)),
                    sa.Column("segredo_hash", sa.String(64)), sa.Column("tentativas", sa.Integer(), nullable=False),
                    sa.Column("expira_em", sa.DateTime(timezone=True), nullable=False), schema="controle",
                    if_not_exists=True)
    op.create_index("ix_controle_portal_acessos_tipo", "portal_acessos", ["tipo"], schema="controle",
                    if_not_exists=True)
    op.create_index("ix_controle_portal_acessos_expira_em", "portal_acessos", ["expira_em"], schema="controle",
                    if_not_exists=True)


def downgrade():
    op.drop_table("portal_acessos", schema="controle")
