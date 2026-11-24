"""PR-B andamento das cargas (CT16–CT22 / UXC10–UXC13). Aditiva e idempotente.

- controle.execucoes ganha: subetapa (texto fixo, sem dado pessoal), subetapa_contador, subetapa_total (nulo =
  total desconhecido), subetapa_iniciada_em e ultimo_sinal_em. A coluna `etapa` já existe desde a 0009.
- controle.execucao_subetapas: log das subetapas (início, fim, contador), só nomes de etapa e contagens.

down_revision = '0033' (ponta da main em 82daff0, depois do #50).

Revision ID: 0035
Revises: 0033
"""
import sqlalchemy as sa
from alembic import op

revision = '0035'
down_revision = '0033'
branch_labels = None
depends_on = None

COLUNAS = (
    ("subetapa", sa.String(200)),
    ("subetapa_contador", sa.BigInteger()),
    ("subetapa_total", sa.BigInteger()),
    ("subetapa_iniciada_em", sa.DateTime(timezone=True)),
    ("ultimo_sinal_em", sa.DateTime(timezone=True)),
)


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    existentes = {c["name"] for c in insp.get_columns("execucoes", schema="controle")}
    for nome, tipo in COLUNAS:
        if nome not in existentes:
            op.add_column("execucoes", sa.Column(nome, tipo, nullable=True), schema="controle")
    if not insp.has_table("execucao_subetapas", schema="controle"):
        op.create_table(
            "execucao_subetapas",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("execucao_id", sa.Integer, sa.ForeignKey("controle.execucoes.id", ondelete="CASCADE"),
                      nullable=False),
            sa.Column("etapa", sa.String(20), nullable=False),
            sa.Column("subetapa", sa.String(200), nullable=False),
            sa.Column("contador", sa.BigInteger),
            sa.Column("total", sa.BigInteger),
            sa.Column("iniciada_em", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("finalizada_em", sa.DateTime(timezone=True)),
            schema="controle",
        )
    op.execute("CREATE INDEX IF NOT EXISTS ix_execucao_subetapas_exec ON controle.execucao_subetapas "
               "(execucao_id, id DESC)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_execucao_subetapas_texto ON controle.execucao_subetapas "
               "(subetapa, id DESC)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS controle.execucao_subetapas")
    for nome, _ in COLUNAS:
        op.execute(f"ALTER TABLE controle.execucoes DROP COLUMN IF EXISTS {nome}")
