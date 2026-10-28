"""Índices em cidadao_id nas tabelas filhas da silver (aditiva: só CREATE INDEX IF NOT EXISTS).

27/10 (ouro travando): vinculos, identificadores e enderecos têm FK cidadao_id -> silver.cidadaos ON DELETE CASCADE,
mas nenhum índice em cidadao_id. Na reconstrução, `DELETE FROM silver.cidadaos` dispara, para CADA cidadão, um
DELETE nas filhas por cidadao_id: sem índice, cada um é uma varredura completa (e, na mesma transação, as linhas
recém-apagadas ainda estão lá como tuplas mortas). Com ~500 mil cidadãos isso é O(n²). O índice (cidadao_id, id) de
vinculos também serve a leitura do ouro em ordem de cidadão.

Por que não CONCURRENTLY: ele espera TODAS as transações abertas no banco (até uma sessão "idle in transaction" de
outro processo) e pode travar o deploy sem prazo; foi o que aconteceu no teste que faz downgrade/upgrade. A silver só
é escrita durante uma reconstrução, e o índice em ~500 mil linhas sai em poucos segundos. Para não ficar esperando
uma reconstrução em andamento, lock_timeout: se não conseguir a trava em 60 s, a migração falha (visível) e é só
rodar de novo com a carga parada. Leituras não são bloqueadas.

Revision ID: 0010
Revises: 0009m
"""
from alembic import op

revision = '0010'
down_revision = '0009m'
branch_labels = None
depends_on = None

INDICES = (
    ("ix_silver_vinculos_cidadao_id_id", "vinculos", "cidadao_id, id"),
    ("ix_silver_identificadores_cidadao_id", "identificadores", "cidadao_id"),
    ("ix_silver_enderecos_cidadao_id", "enderecos", "cidadao_id"),
)


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '60s'")
    for nome, tabela, cols in INDICES:
        op.execute(f"CREATE INDEX IF NOT EXISTS {nome} ON silver.{tabela} ({cols})")


def downgrade() -> None:
    for nome, _tabela, _cols in INDICES:
        op.execute(f"DROP INDEX IF EXISTS silver.{nome}")
