"""Parte 2 de desempenho: prata e ouro incrementais (PI1–PI14). Aditiva e idempotente.

- controle.camadas_marca: marca d'água por (sistema, tabela) — `ultimo_id` (último id da bronze consumido por uma
  execução que terminou com sucesso; só avança no fim) e `expandido_ate` (até onde o delta já virou pendência,
  para retomar sem repetir). Linhas especiais `_regras`/`_pessoas` guardam assinaturas em `assinatura`.
- controle.camadas_pendencias: fila durável de chaves a recalcular (tipo, chave). Cada lote apaga as suas no mesmo
  commit em que grava a prata/ouro: chave no estado antigo ou no novo, nunca pela metade; a retomada segue daqui.
- controle.execucoes: camadas_modo ('incremental' | 'recomposicao') e camadas_contagens (novas, alteradas,
  sem_alteracao, excluidas).
- bronze.ultima_versao (TL 30/10 09h04): PK (sistema_origem, tabela, id_origem) -> registro_id da versão mais recente
  SEM quarentena. A carga mantém por INSERT ... ON CONFLICT DO UPDATE; o preenchimento inicial roda fora do boot, em
  lotes e idempotente (app.services.ultima_versao / scripts/preenche_ultima_versao.py). Uma versão em quarentena não
  atualiza a tabela. O incremental lê registro_id > marca d'água.
- Índices (CREATE INDEX CONCURRENTLY IF NOT EXISTS, lock_timeout, padrão do #59): ultima_versao por
  (sistema, tabela, registro_id) para ler o delta; bronze IPTU pelo CODI normalizado (posses e imóvel de um CODI); prata por contribuinte e matrícula.
bronze.registros.hash_conteudo (D4, ajuste do TL 08h57): nulo aceito, preenchido só nas próximas cargas (sha256 do
JSON de `dados`, mesmo quando a origem usa rowversion), SEM backfill. ADD COLUMN nulo sem default: só metadado.
A detecção de mudança continua no `hash` atual. D6: predicado único quarentena IS NULL AND ambiente_origem='producao'.

Cadeia: 0035 -> 0037 -> 0036 -> 0038. down_revision = '0036' (#59).

Revision ID: 0038
Revises: 0036
"""
import logging
import os
import re

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = '0038'
down_revision = '0036'
branch_labels = None
depends_on = None

log = logging.getLogger("alembic.runtime.migration")
PADRAO_LOCK_TIMEOUT, TETO_LOCK_TIMEOUT, TETO_MS = "30s", "10min", 10 * 60 * 1000
_FATOR_MS = {"ms": 1, "s": 1000, "min": 60_000}
_TRAVA = {"55P03", "57014"}

INDICES = (  # nome, schema, definição
    # TL 30/10 + D6: predicado único (ambiente_origem como predicado, não coluna); serve _ultimas_versoes()
    ("ix_bronze_registros_ultima", "bronze", "bronze.registros (sistema_origem, tabela, id_origem, id DESC) "
     "WHERE quarentena IS NULL AND ambiente_origem = 'producao'"),
    ("ix_ultima_versao_registro", "bronze", "bronze.ultima_versao (sistema_origem, tabela, registro_id)"),
    ("ix_registros_iptu_codi", "bronze",
     "bronze.registros (tabela, left(btrim(dados->>'CODI'), 22)) WHERE sistema_origem = 'IPTU'"),
    ("ix_silver_lancamento_contrib", "silver", "silver.lancamentos (contribuinte)"),
    ("ix_silver_atendimento_matricula", "silver", "silver.atendimentos (matricula)"),
    ("ix_silver_agendamento_matricula", "silver", "silver.agendamentos (matricula)"),
    ("ix_silver_vacinacao_matricula", "silver", "silver.vacinacoes (matricula)"),
)


def _concorrente() -> str:
    """Como na 0036: sem CONCURRENTLY só na suíte (APP_ENV=test), salvo SIGPMF_INDICE_CONCORRENTE=1."""
    if os.environ.get("APP_ENV") == "test" and os.environ.get("SIGPMF_INDICE_CONCORRENTE") != "1":
        return ""
    return "CONCURRENTLY "


def _lock_timeout() -> str:
    """SIGPMF_INDICE_LOCK_TIMEOUT (padrão 30s; zero/inválido -> 30s; acima de 10min -> 10min), aviso sem eco."""
    bruto = os.environ.get("SIGPMF_INDICE_LOCK_TIMEOUT")
    if bruto is None or not bruto.strip():
        return PADRAO_LOCK_TIMEOUT
    m = re.fullmatch(r"(\d+)(ms|s|min)?", bruto.strip())
    if not m or int(m.group(1)) <= 0:
        log.warning("SIGPMF_INDICE_LOCK_TIMEOUT inválido ou zero (espera infinita); usando %s", PADRAO_LOCK_TIMEOUT)
        return PADRAO_LOCK_TIMEOUT
    if int(m.group(1)) * _FATOR_MS[m.group(2) or "ms"] > TETO_MS:
        log.warning("SIGPMF_INDICE_LOCK_TIMEOUT acima do teto; usando %s", TETO_LOCK_TIMEOUT)
        return TETO_LOCK_TIMEOUT
    return f"{int(m.group(1))}{m.group(2) or 'ms'}"


def _executa(sql: str, acao: str, nome: str, lt: str) -> None:
    try:
        op.execute(sql)
    except sa.exc.DBAPIError as exc:
        if getattr(getattr(exc, "orig", None), "sqlstate", None) in _TRAVA:
            raise RuntimeError(
                f"Migração 0038: não foi possível {acao} o índice {nome} em {lt} porque há transação longa aberta. "
                "Rode de novo fora da janela de cargas (o índice inválido que sobrar é descartado e recriado na "
                "próxima execução); ajuste SIGPMF_INDICE_LOCK_TIMEOUT se precisar.") from None
        raise


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if not insp.has_table("camadas_marca", schema="controle"):
        op.create_table(
            "camadas_marca",
            sa.Column("sistema", sa.String(50), primary_key=True),
            sa.Column("tabela", sa.String(200), primary_key=True),
            sa.Column("ultimo_id", sa.BigInteger, nullable=False, server_default="0"),
            sa.Column("expandido_ate", sa.BigInteger, nullable=False, server_default="0"),
            sa.Column("assinatura", sa.Text),
            sa.Column("execucao_id", sa.Integer),
            sa.Column("atualizado_em", sa.DateTime(timezone=True), server_default=sa.func.now()),
            schema="controle")
    if not insp.has_table("ultima_versao", schema="bronze"):  # TL 09h04: núcleo do incremental
        op.create_table(
            "ultima_versao",
            sa.Column("sistema_origem", sa.String(50), primary_key=True),
            sa.Column("tabela", sa.String(200), primary_key=True),
            sa.Column("id_origem", sa.String(200), primary_key=True),
            sa.Column("registro_id", sa.BigInteger, nullable=False),
            schema="bronze")
    if not insp.has_table("camadas_pendencias", schema="controle"):
        op.create_table(
            "camadas_pendencias",
            sa.Column("tipo", sa.String(20), primary_key=True),
            sa.Column("chave", sa.String(250), primary_key=True),
            sa.Column("criado_em", sa.DateTime(timezone=True), server_default=sa.func.now()),
            schema="controle")
    if "hash_conteudo" not in {c["name"] for c in insp.get_columns("registros", schema="bronze")}:
        op.add_column("registros", sa.Column("hash_conteudo", sa.String(64)), schema="bronze")  # D4: sem backfill
    cols = {c["name"] for c in insp.get_columns("execucoes", schema="controle")}
    if "camadas_modo" not in cols:
        op.add_column("execucoes", sa.Column("camadas_modo", sa.String(20)), schema="controle")
    if "camadas_contagens" not in cols:
        op.add_column("execucoes", sa.Column("camadas_contagens", postgresql.JSONB), schema="controle")
    lt = _lock_timeout()
    with op.get_context().autocommit_block():
        op.execute(f"SET lock_timeout = '{lt}'")
        try:
            for nome, schema, defin in INDICES:
                invalido = op.get_bind().execute(sa.text(
                    "SELECT 1 FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid "
                    "JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE n.nspname = :s AND c.relname = :n AND NOT i.indisvalid"), {"s": schema, "n": nome}).first()
                if invalido:
                    _executa(f"DROP INDEX {_concorrente()}IF EXISTS {schema}.{nome}", "descartar", nome, lt)
                _executa(f"CREATE INDEX {_concorrente()}IF NOT EXISTS {nome} ON {defin}", "criar", nome, lt)
        finally:
            op.execute("RESET lock_timeout")


def downgrade() -> None:
    with op.get_context().autocommit_block():
        for nome, schema, _d in INDICES:
            op.execute(f"DROP INDEX {_concorrente()}IF EXISTS {schema}.{nome}")
    op.execute("ALTER TABLE controle.execucoes DROP COLUMN IF EXISTS camadas_contagens")
    op.execute("ALTER TABLE controle.execucoes DROP COLUMN IF EXISTS camadas_modo")
    op.execute("ALTER TABLE bronze.registros DROP COLUMN IF EXISTS hash_conteudo")
    op.execute("DROP TABLE IF EXISTS controle.camadas_pendencias")
    op.execute("DROP TABLE IF EXISTS bronze.ultima_versao")
    op.execute("DROP TABLE IF EXISTS controle.camadas_marca")
