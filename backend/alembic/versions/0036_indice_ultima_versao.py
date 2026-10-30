"""Desempenho prata P1: índice parcial para a última versão de cada chave da bronze (`_ultimas_versoes`).

Aditiva e idempotente: CREATE INDEX CONCURRENTLY IF NOT EXISTS fora da transação (autocommit_block), sem travar
escrita na bronze. Downgrade: DROP INDEX CONCURRENTLY IF EXISTS.

down_revision = '0037' (ponta da main depois do #57, b518060).

Revision ID: 0036
Revises: 0037
"""
import logging
import os
import re

import sqlalchemy as sa
from alembic import op

revision = '0036'
down_revision = '0037'
branch_labels = None
depends_on = None

INDICE = "ix_registros_ultima_versao"
log = logging.getLogger("alembic.runtime.migration")


def _concorrente() -> str:
    """CONCURRENTLY fora de teste (não trava a escrita na bronze). Na suíte, testes que refazem downgrade/upgrade no
    banco compartilhado têm sessões com transação aberta, e o CONCURRENTLY esperaria por elas para sempre: lá o índice
    é criado sem CONCURRENTLY, a menos que SIGPMF_INDICE_CONCORRENTE=1 (o teste da 0036 força o caminho real)."""
    if os.environ.get("APP_ENV") == "test" and os.environ.get("SIGPMF_INDICE_CONCORRENTE") != "1":
        return ""
    return "CONCURRENTLY "


PADRAO_LOCK_TIMEOUT = "30s"
TETO_LOCK_TIMEOUT = "10min"
TETO_LOCK_TIMEOUT_MS = 10 * 60 * 1000
_FATOR_MS = {"ms": 1, "s": 1000, "min": 60_000}
_TRAVA = {"55P03", "57014"}  # lock_not_available, query_canceled (timeout)


def _lock_timeout() -> str:
    """Espera máxima por travas (SIGPMF_INDICE_LOCK_TIMEOUT, padrão 30s): sem isso o CONCURRENTLY espera para sempre
    uma transação longa aberta na bronze e trava o boot. Valor inválido ou zero (0 no Postgres = espera infinita) volta
    ao padrão; acima de 10min vira 10min. Os dois com aviso no log que não repete o valor."""
    bruto = os.environ.get("SIGPMF_INDICE_LOCK_TIMEOUT")
    if bruto is None or not bruto.strip():
        return PADRAO_LOCK_TIMEOUT
    m = re.fullmatch(r"(\d+)(ms|s|min)?", bruto.strip())
    if not m or int(m.group(1)) <= 0:
        log.warning("SIGPMF_INDICE_LOCK_TIMEOUT inválido ou zero (espera infinita); usando %s", PADRAO_LOCK_TIMEOUT)
        return PADRAO_LOCK_TIMEOUT
    ms = int(m.group(1)) * _FATOR_MS[m.group(2) or "ms"]
    if ms > TETO_LOCK_TIMEOUT_MS:  # acima do teto (inclui valores que estourariam o int do Postgres -> DataError)
        log.warning("SIGPMF_INDICE_LOCK_TIMEOUT acima do teto; usando %s", TETO_LOCK_TIMEOUT)
        return TETO_LOCK_TIMEOUT
    return f"{int(m.group(1))}{m.group(2) or 'ms'}"


def _falha_trava(acao: str, lt: str) -> RuntimeError:
    return RuntimeError(
        f"Migração 0036: não foi possível {acao} o índice bronze.{INDICE} em {lt} porque há transação longa aberta "
        "na bronze. Rode de novo fora da janela de cargas (o índice inválido que sobrar é descartado e recriado na "
        "próxima execução); ajuste SIGPMF_INDICE_LOCK_TIMEOUT se precisar.")


def _executa(sql: str, acao: str, lt: str) -> None:
    try:
        op.execute(sql)
    except sa.exc.DBAPIError as exc:
        if getattr(getattr(exc, "orig", None), "sqlstate", None) in _TRAVA:
            raise _falha_trava(acao, lt) from None  # sem a exceção crua, sem str(exc), sem DSN
        raise


def upgrade() -> None:
    lt = _lock_timeout()
    with op.get_context().autocommit_block():
        op.execute(f"SET lock_timeout = '{lt}'")
        try:
            # um CONCURRENTLY interrompido deixa o índice INVALID (e o IF NOT EXISTS o manteria): descarta e recria
            invalido = op.get_bind().execute(sa.text(
                "SELECT 1 FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = 'bronze' AND c.relname = :n AND NOT i.indisvalid"), {"n": INDICE}).first()
            if invalido:
                _executa(f"DROP INDEX {_concorrente()}IF EXISTS bronze.{INDICE}", "descartar", lt)
            _executa(f"CREATE INDEX {_concorrente()}IF NOT EXISTS {INDICE} ON bronze.registros "
                     "(sistema_origem, tabela, id_origem, id DESC) WHERE quarentena IS NULL", "criar", lt)
        finally:
            op.execute("RESET lock_timeout")


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(f"DROP INDEX {_concorrente()}IF EXISTS bronze.{INDICE}")
