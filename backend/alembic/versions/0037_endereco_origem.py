"""Endereço na ficha (EN1–EN14). Aditiva e idempotente.

- ref.geral_rua (Geral.dbo.Rua por CD_RUA, com Tipo_Logradouro já resolvido) e ref.geral_bairro (Geral.dbo.Bairro
  por CD_BAI): só referência de logradouro/bairro, sem dado pessoal.
- silver.enderecos ganha colunas estruturadas: tipo_logradouro, numero, complemento, cidade, uf, codigo_ibge e
  origem_endereco ('geral' | 'outro' | 'nenhum').

Cadeia: 0035 -> 0037 -> 0036. down_revision = '0035'. A 0037 entrou na main pelo #62 (2ae4703); a 0036 (#59,
índice da última versão na bronze) vem depois dela e é a head única.

Revision ID: 0037
Revises: 0035
"""
from alembic import op

revision = '0037'
down_revision = '0035'
branch_labels = None
depends_on = None

COLUNAS_ENDERECO = (
    ("tipo_logradouro", "VARCHAR(80)"),
    ("numero", "VARCHAR(30)"),
    ("complemento", "VARCHAR(200)"),
    ("cidade", "VARCHAR(120)"),
    ("uf", "CHAR(2)"),
    ("codigo_ibge", "CHAR(7)"),
    ("origem_endereco", "VARCHAR(10)"),
)


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS ref")
    op.execute("""CREATE TABLE IF NOT EXISTS ref.geral_rua (
        cd_rua INTEGER PRIMARY KEY, tipo_logradouro VARCHAR(80), logradouro VARCHAR(300), cep CHAR(8),
        cidade VARCHAR(120), uf CHAR(2), codigo_ibge CHAR(7), atualizado_em TIMESTAMPTZ DEFAULT now())""")
    op.execute("""CREATE TABLE IF NOT EXISTS ref.geral_bairro (
        cd_bai INTEGER PRIMARY KEY, bairro VARCHAR(200), cidade VARCHAR(120), atualizado_em TIMESTAMPTZ DEFAULT now())""")
    for nome, tipo in COLUNAS_ENDERECO:
        op.execute(f"ALTER TABLE silver.enderecos ADD COLUMN IF NOT EXISTS {nome} {tipo}")


def downgrade() -> None:
    for nome, _ in COLUNAS_ENDERECO:
        op.execute(f"ALTER TABLE silver.enderecos DROP COLUMN IF EXISTS {nome}")
    op.execute("DROP TABLE IF EXISTS ref.geral_bairro")
    op.execute("DROP TABLE IF EXISTS ref.geral_rua")
