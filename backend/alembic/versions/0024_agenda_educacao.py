"""Educação (GestaoEscolar + CoreSSO) na agenda das cargas: agendamento DESLIGADO. Aditiva.

Só insere a linha `educacao` em controle.agendamentos (04:00, ativo=false, ordem 7); nunca altera as existentes.
O DevOps liga pela tela depois da carga completa. O downgrade não apaga nada (a linha desligada é inofensiva).

Revision ID: 0024
Revises: 0023
"""
from alembic import op

revision = '0024'
down_revision = '0023'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        INSERT INTO controle.agendamentos (conector, horario, ativo, ordem)
        VALUES ('educacao', '04:00', false, 7)
        ON CONFLICT (conector) DO NOTHING
    """)


def downgrade() -> None:
    pass  # sem exclusão física: o agendamento desligado fica na tabela
