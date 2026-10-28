"""Tributário (onda 2, TR1–TR8): prata de imóveis, possuidores, lançamentos e dívidas; conectores no catálogo e na agenda.

Aditiva: tabelas novas no schema silver; IPTU, Divida e Financeiro passam a 'implementado' no catálogo (como a 0006
fez com o ISS); agendamentos desligados para iptu (04:00), divida (04:30) e financeiro (05:00).

Revision ID: 0013
Revises: 0012
"""
import sqlalchemy as sa
from alembic import op

revision = '0013'
down_revision = '0012'
branch_labels = None
depends_on = None

BASES = (("IPTU", "iptu", "IPTU"), ("Divida", "divida", "DIVIDA"), ("Financeiro", "financeiro", "FINANCEIRO"))


def upgrade() -> None:
    op.create_table(
        "imoveis",
        sa.Column("codi", sa.String(22), primary_key=True),
        sa.Column("inscricao", sa.String(30)), sa.Column("numero", sa.String(20)), sa.Column("complemento", sa.String(200)),
        sa.Column("cd_bairro", sa.Integer()), sa.Column("quadra", sa.String(200)), sa.Column("lote", sa.String(200)),
        sa.Column("area_terreno", sa.Float()), sa.Column("iptu_anual", sa.Numeric(14, 2)), sa.Column("situacao", sa.String(20)),
        sa.Column("atualizado_em", sa.DateTime(timezone=True)),
        schema="silver")
    op.create_table(
        "imovel_possuidores",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("codi", sa.String(22), nullable=False), sa.Column("documento", sa.String(20), nullable=False),
        sa.Column("tipo_pessoa", sa.String(2), nullable=False), sa.Column("cidadao_id", sa.String(36)),
        sa.Column("tipo_posse", sa.Integer()), sa.Column("desde", sa.DateTime(timezone=True)),
        sa.Column("ate", sa.DateTime(timezone=True)), sa.Column("ativo", sa.Boolean(), nullable=False),
        schema="silver")
    op.create_index("ix_silver_possuidor_cidadao", "imovel_possuidores", ["cidadao_id"], schema="silver")
    op.create_index("ix_silver_possuidor_codi", "imovel_possuidores", ["codi"], schema="silver")
    op.create_table(
        "lancamentos",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("chave_origem", sa.String(200), nullable=False, unique=True),
        sa.Column("exercicio", sa.Integer(), nullable=False), sa.Column("tributo", sa.String(10)),
        sa.Column("receita", sa.String(10)), sa.Column("parcela", sa.Integer()), sa.Column("contribuinte", sa.String(20)),
        sa.Column("codi", sa.String(22)), sa.Column("cidadao_id", sa.String(36)), sa.Column("vencimento", sa.Date()),
        sa.Column("valor", sa.Numeric(14, 2)), sa.Column("valor_pago", sa.Numeric(14, 2)), sa.Column("pagamento", sa.Date()),
        sa.Column("pago", sa.Boolean(), nullable=False), sa.Column("status", sa.String(50)),
        schema="silver")
    op.create_index("ix_silver_lancamento_cidadao", "lancamentos", ["cidadao_id"], schema="silver")
    op.create_index("ix_silver_lancamento_venc", "lancamentos", ["vencimento"], schema="silver")
    op.create_table(
        "dividas",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("chave_origem", sa.String(200), nullable=False, unique=True),
        sa.Column("codi", sa.String(22), nullable=False), sa.Column("ano", sa.Integer()), sa.Column("natureza", sa.Integer()),
        sa.Column("cidadao_id", sa.String(36)), sa.Column("inscricao", sa.Date()), sa.Column("vencimento", sa.Date()),
        sa.Column("valor_origem", sa.Numeric(14, 2)), sa.Column("saldo", sa.Numeric(14, 2)),
        sa.Column("pago_total", sa.Numeric(14, 2)), sa.Column("status", sa.String(5)), sa.Column("situacao", sa.Integer()),
        sa.Column("protestos", sa.Integer()), sa.Column("processo", sa.String(30)),
        sa.Column("extinta", sa.Boolean(), nullable=False),
        schema="silver")
    op.create_index("ix_silver_divida_cidadao", "dividas", ["cidadao_id"], schema="silver")
    op.create_index("ix_silver_divida_codi", "dividas", ["codi"], schema="silver")
    for nome, conjunto, sistema in BASES:
        op.execute(sa.text("""
            UPDATE controle.bases_catalogo
               SET status = 'implementado', conjunto = :c, sistemas = CAST(:s AS jsonb), atualizado_em = now()
             WHERE nome = :n AND (status <> 'implementado' OR conjunto IS DISTINCT FROM :c)
        """).bindparams(c=conjunto, s=f'["{sistema}"]', n=nome))
    op.execute("""
        INSERT INTO controle.agendamentos (conector, horario, ativo, ordem)
        VALUES ('iptu', '04:00', false, 3), ('divida', '04:30', false, 4), ('financeiro', '05:00', false, 5)
        ON CONFLICT (conector) DO NOTHING
    """)


def downgrade() -> None:
    op.execute("DELETE FROM controle.agendamentos WHERE conector IN ('iptu', 'divida', 'financeiro')")
    for nome, conjunto, _s in BASES:
        op.execute(sa.text("UPDATE controle.bases_catalogo SET status = 'mapeado', conjunto = NULL, atualizado_em = now() "
                           "WHERE nome = :n AND conjunto = :c").bindparams(n=nome, c=conjunto))
    for t in ("dividas", "lancamentos", "imovel_possuidores", "imoveis"):
        op.drop_table(t, schema="silver")
