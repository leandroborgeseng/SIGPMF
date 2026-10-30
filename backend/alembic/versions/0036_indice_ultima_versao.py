"""Desempenho prata P1: índice parcial para a última versão de cada chave da bronze (`_ultimas_versoes`).

Aditiva e idempotente: CREATE INDEX CONCURRENTLY IF NOT EXISTS fora da transação (autocommit_block), sem travar
escrita na bronze. Downgrade: DROP INDEX CONCURRENTLY IF EXISTS.

down_revision = '0035' (ponta da main depois do #57, b518060).

Revision ID: 0036
Revises: 0035
"""
import os
import re

import sqlalchemy as sa
from alembic import op

revision = '0036'
down_revision = '0035'
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


def _lock_timeout() -> str:
    """Espera máxima por travas (SIGPMF_INDICE_LOCK_TIMEOUT, padrão 30s): sem isso o CONCURRENTLY espera para sempre
    uma transação longa aberta na bronze e trava o boot."""
    v = (os.environ.get("SIGPMF_INDICE_LOCK_TIMEOUT") or "30s").strip()
    if not re.fullmatch(r"\d+(ms|s|min)?", v):
        v = "30s"
    return v


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(f"SET lock_timeout = '{_lock_timeout()}'")
        # um CONCURRENTLY interrompido deixa o índice INVALID (e o IF NOT EXISTS o manteria): descarta e recria
        invalido = op.get_bind().execute(sa.text(
            "SELECT 1 FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'bronze' AND c.relname = :n AND NOT i.indisvalid"), {"n": INDICE}).first()
        if invalido:
            op.execute(f"DROP INDEX {_concorrente()}IF EXISTS bronze.{INDICE}")
        try:
            op.execute(f"CREATE INDEX {_concorrente()}IF NOT EXISTS {INDICE} ON bronze.registros "
                       "(sistema_origem, tabela, id_origem, id DESC) WHERE quarentena IS NULL")
        except sa.exc.OperationalError as exc:
            if getattr(getattr(exc, "orig", None), "sqlstate", None) == "55P03":  # lock_not_available
                raise RuntimeError(
                    f"Migração 0036: o índice bronze.{INDICE} não foi criado em {_lock_timeout()} porque há transação "
                    "longa aberta na bronze. Rode de novo fora da janela de cargas (o índice inválido que sobrar é "
                    "descartado e recriado na próxima execução); ajuste SIGPMF_INDICE_LOCK_TIMEOUT se precisar."
                ) from None
            raise
        finally:
            op.execute("RESET lock_timeout")


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(f"DROP INDEX {_concorrente()}IF EXISTS bronze.{INDICE}")
