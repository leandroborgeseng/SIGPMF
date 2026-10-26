"""ISS passa a Pronto no catálogo (dados; PR #8)

ISS.Municipes tem política aprovada (#6) e carga real via POST /api/conectores/iss/amostra (#8).
Idempotente: só altera a linha 'ISS'. Downgrade devolve para 'mapeado' sem conjunto. Nunca apaga linhas.

Revision ID: 0006
Revises: 0005
"""
from alembic import op

revision = '0006'
down_revision = '0005'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        UPDATE controle.bases_catalogo
           SET status = 'implementado', conjunto = 'iss', sistemas = '["ISS"]'::jsonb, atualizado_em = now()
         WHERE nome = 'ISS' AND (status <> 'implementado' OR conjunto IS DISTINCT FROM 'iss')
    """)


def downgrade() -> None:
    op.execute("""
        UPDATE controle.bases_catalogo
           SET status = 'mapeado', conjunto = NULL, atualizado_em = now()
         WHERE nome = 'ISS' AND status = 'implementado'
    """)
