"""Desempenho prata P1: índice parcial para a última versão de cada chave da bronze (`_ultimas_versoes`).

Aditiva e idempotente: CREATE INDEX CONCURRENTLY IF NOT EXISTS fora da transação (autocommit_block), sem travar
escrita na bronze. Downgrade: DROP INDEX CONCURRENTLY IF EXISTS.

down_revision = '0033' (ponta da main em 648cb1f). Se o #57 (0035 -> 0033) entrar antes, repontar para '0035'.

Revision ID: 0036
Revises: 0033
"""
import os

import sqlalchemy as sa
from alembic import op

revision = '0036'
down_revision = '0033'
branch_labels = None
depends_on = None

INDICE = "ix_registros_ultima_versao"


def _concorrente() -> str:
    """CONCURRENTLY fora de teste (não trava a escrita na bronze). Na suíte, testes que refazem downgrade/upgrade no
    banco compartilhado têm sessões com transação aberta, e o CONCURRENTLY esperaria por elas para sempre: lá o índice
    é criado sem CONCURRENTLY, a menos que SIGPMF_INDICE_CONCORRENTE=1 (o teste da 0036 força o caminho real)."""
    if os.environ.get("APP_ENV") == "test" and os.environ.get("SIGPMF_INDICE_CONCORRENTE") != "1":
        return ""
    return "CONCURRENTLY "


def upgrade() -> None:
    with op.get_context().autocommit_block():
        # um CONCURRENTLY interrompido deixa o índice INVALID (e o IF NOT EXISTS o manteria): descarta e recria
        invalido = op.get_bind().execute(sa.text(
            "SELECT 1 FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'bronze' AND c.relname = :n AND NOT i.indisvalid"), {"n": INDICE}).first()
        if invalido:
            op.execute(f"DROP INDEX {_concorrente()}IF EXISTS bronze.{INDICE}")
        op.execute(f"CREATE INDEX {_concorrente()}IF NOT EXISTS {INDICE} ON bronze.registros "
                   "(sistema_origem, tabela, id_origem, id DESC) WHERE quarentena IS NULL")


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(f"DROP INDEX {_concorrente()}IF EXISTS bronze.{INDICE}")
