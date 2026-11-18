"""Índice da inscrição só com dígitos em bronze.sil_imoveis_fisico. Aditiva.

A linha do tempo casa o CODI do possuidor com inscrfisico (`01.1.11.12.002.25.05` → `01111120022505`).
regexp_replace não é IMMUTABLE; a função abaixo é, para o índice de expressão existir.
Não cria coluna, não apaga linha e não lê documento nem nome.

Revision ID: 0048
Revises: 0047
"""
from alembic import op

revision = "0048"
down_revision = "0047"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '60s'")
    op.execute(r"""
        CREATE OR REPLACE FUNCTION bronze.inscricao_so_digitos(t text)
        RETURNS text
        LANGUAGE sql
        IMMUTABLE
        PARALLEL SAFE
        AS $$ SELECT regexp_replace(COALESCE(t, ''), '\D', '', 'g') $$
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_sil_imoveis_fisico_inscr_digitos "
        "ON bronze.sil_imoveis_fisico (bronze.inscricao_so_digitos(inscrfisico))"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS bronze.ix_sil_imoveis_fisico_inscr_digitos")
    op.execute("DROP FUNCTION IF EXISTS bronze.inscricao_so_digitos(text)")
