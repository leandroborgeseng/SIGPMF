"""catálogo de bases (controle.bases_catalogo) + seed idempotente

Aditiva: só cria a tabela e faz upsert. Downgrade remove apenas a tabela nova.
Encadeamento: aponta para 0003 (main em df9ac51). Se o PR #6 (0004_municipe_iss) entrar antes,
trocar down_revision para '0004'.

Revision ID: 0005
Revises: 0003
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.catalogo.bases import semeia

revision = '0005'
down_revision = '0003'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('bases_catalogo',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('nome', sa.String(length=100), nullable=False),
    sa.Column('grupo', sa.String(length=30), nullable=False),
    sa.Column('triagem', sa.String(length=20), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('prioridade', sa.Integer(), nullable=True),
    sa.Column('sistemas', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
    sa.Column('conjunto', sa.String(length=50), nullable=True),
    sa.Column('descricao', sa.Text(), nullable=True),
    sa.Column('criado_em', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('atualizado_em', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('nome'),
    sa.CheckConstraint("status IN ('implementado','mapeado','triado','excluido')", name='ck_bases_catalogo_status'),
    schema='controle'
    )
    semeia(op.get_bind())


def downgrade() -> None:
    op.drop_table('bases_catalogo', schema='controle')
