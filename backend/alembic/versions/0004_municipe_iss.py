"""municipe iss (aditiva)

Revision ID: 0004
Revises: 0003
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('execucoes', sa.Column('duplicados', sa.Integer(), server_default='0', nullable=False), schema='controle')
    op.add_column('execucoes', sa.Column('pendentes', sa.Integer(), server_default='0', nullable=False), schema='controle')
    op.create_table('municipes',
    sa.Column('documento', sa.String(length=14), nullable=False),
    sa.Column('tipo', sa.String(length=10), nullable=False),
    sa.Column('tipo_pessoa', sa.String(length=1), nullable=False),
    sa.Column('nome', sa.String(length=300), nullable=True),
    sa.Column('nome_mae', sa.String(length=300), nullable=True),
    sa.Column('data_nascimento', sa.Date(), nullable=True),
    sa.Column('pis_pasep', sa.String(length=20), nullable=True),
    sa.Column('data_alteracao', sa.DateTime(), nullable=True),
    sa.Column('cidadao_id', sa.String(length=36), nullable=True),
    sa.Column('confianca', sa.Float(), nullable=True),
    sa.Column('hash', sa.String(length=64), nullable=False),
    sa.Column('execucao_id', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['execucao_id'], ['controle.execucoes.id']),
    sa.PrimaryKeyConstraint('documento'),
    schema='silver'
    )
    op.create_index('ix_silver_municipes_cidadao_id', 'municipes', ['cidadao_id'], schema='silver')
    op.create_table('municipes_versoes',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('documento', sa.String(length=14), nullable=False),
    sa.Column('hash', sa.String(length=64), nullable=False),
    sa.Column('dados', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('execucao_id', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['execucao_id'], ['controle.execucoes.id']),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('documento', 'hash'),
    schema='silver'
    )
    op.create_index('ix_silver_municipes_versoes_documento', 'municipes_versoes', ['documento'], schema='silver')
    op.create_table('municipes_pendencias',
    sa.Column('chave', sa.String(length=32), nullable=False),
    sa.Column('motivo', sa.String(length=50), nullable=False),
    sa.Column('tipo_pessoa', sa.String(length=1), nullable=True),
    sa.Column('nome', sa.String(length=300), nullable=True),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('execucao_id', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['execucao_id'], ['controle.execucoes.id']),
    sa.PrimaryKeyConstraint('chave'),
    schema='silver'
    )


def downgrade() -> None:
    op.drop_table('municipes_pendencias', schema='silver')
    op.drop_index('ix_silver_municipes_versoes_documento', table_name='municipes_versoes', schema='silver')
    op.drop_table('municipes_versoes', schema='silver')
    op.drop_index('ix_silver_municipes_cidadao_id', table_name='municipes', schema='silver')
    op.drop_table('municipes', schema='silver')
    op.drop_column('execucoes', 'pendentes', schema='controle')
    op.drop_column('execucoes', 'duplicados', schema='controle')
