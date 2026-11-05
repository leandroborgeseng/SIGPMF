"""Shell de painéis (onda 0): gold.painel_fato, fato agregado por competência e níveis de drill-down. Sem pessoa.

Aditiva e idempotente: só cria tabela e índice; não apaga nada e não liga agendamento.

Revision ID: 0044
Revises: 0043
"""
from alembic import op

revision = '0044'
down_revision = '0043'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""CREATE TABLE IF NOT EXISTS gold.painel_fato (
        id BIGSERIAL PRIMARY KEY,
        painel VARCHAR(40) NOT NULL,
        competencia CHAR(7) NOT NULL,
        nivel1 VARCHAR(80) NOT NULL,
        nivel2 VARCHAR(120) NOT NULL DEFAULT '',
        nivel3 VARCHAR(120) NOT NULL DEFAULT '',
        m1 NUMERIC NOT NULL DEFAULT 0,
        m2 NUMERIC NOT NULL DEFAULT 0,
        n INTEGER NOT NULL DEFAULT 0,
        exemplo BOOLEAN NOT NULL DEFAULT false,
        execucao_id INTEGER)""")
    op.execute("CREATE INDEX IF NOT EXISTS ix_painel_fato_painel_comp ON gold.painel_fato (painel, competencia, nivel1)")


def downgrade() -> None:
    # Aditiva: o downgrade não apaga dados do ouro (remoção, se um dia preciso, é decisão do DBA).
    pass
