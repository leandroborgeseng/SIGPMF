"""API SIL (tributário novo): base no catálogo como implementada e agendamento desligado. Aditiva.

Revision ID: 0015
Revises: 0014
"""
from alembic import op

from app.catalogo.bases import semeia

revision = '0015'
down_revision = '0014'
branch_labels = None
depends_on = None


def upgrade() -> None:
    semeia(op.get_bind())  # só insere bases novas (aqui: "API SIL"); nunca altera as existentes
    op.execute("""
        INSERT INTO controle.agendamentos (conector, horario, ativo, ordem)
        VALUES ('sil', '05:30', false, 6)
        ON CONFLICT (conector) DO NOTHING
    """)


def downgrade() -> None:
    op.execute("DELETE FROM controle.agendamentos WHERE conector = 'sil'")
    op.execute("DELETE FROM controle.bases_catalogo WHERE nome = 'API SIL'")
