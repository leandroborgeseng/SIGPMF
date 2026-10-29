"""Educação: no máximo uma carga completa do conector de educação em andamento por sistema (índice único parcial).

Aditiva. Antes de criar o índice, confere se já existem duplicatas em_andamento; se existirem, falha com mensagem
(não apaga nem altera nenhuma linha: resolva com POST /api/educacao/conector/execucoes/{id}/marcar-falha).
Downgrade remove só o índice.

Revision ID: 0024
Revises: 0023
"""
import sqlalchemy as sa
from alembic import op

revision = '0024'
down_revision = '0023'
branch_labels = None
depends_on = None

INDICE = "uq_execucoes_educacao_completa_em_andamento"


def upgrade() -> None:
    dup = op.get_bind().execute(sa.text(
        "SELECT sistema, count(*) FROM controle.execucoes WHERE status = 'em_andamento' "
        "AND tipo = 'carga_gestaoescolar' GROUP BY sistema HAVING count(*) > 1")).fetchall()
    if dup:
        raise RuntimeError(f"0024: execuções em_andamento duplicadas da educação ({len(dup)} sistema(s)); "
                           "marque as órfãs como falha antes de migrar.")
    op.execute(f"CREATE UNIQUE INDEX IF NOT EXISTS {INDICE} ON controle.execucoes (sistema) "
               "WHERE status = 'em_andamento' AND tipo = 'carga_gestaoescolar'")


def downgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS controle.{INDICE}")
