"""AUDESP (AU1–AU3): prata de finanças públicas + controle dos arquivos + agendamento DESLIGADO. Aditiva e idempotente.

- controle.audesp_arquivos: último arquivo carregado por (tipo, ano), com a assinatura do TCE e o SHA-256.
- silver.despesa, silver.receita, silver.restos_a_pagar: foto do último arquivo de cada ano (sem pessoa ligada).
- controle.agendamentos: linha `audesp` (05:00, ativo=false, ordem 8). O DevOps liga pela tela.

down_revision = '0037' (ponta da main em 3ee4e02). Se a 0036 (#59) entrar antes, quem entrar por último ajusta.

Revision ID: 0038
Revises: 0037
"""
import sqlalchemy as sa
from alembic import op

revision = '0038'
down_revision = '0037'
branch_labels = None
depends_on = None

V = sa.Numeric(18, 2)


def _s(n: int):
    return sa.String(n)


def upgrade() -> None:
    op.create_table(
        "audesp_arquivos",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tipo", _s(20), nullable=False), sa.Column("ano", sa.Integer(), nullable=False),
        sa.Column("origem", _s(10), nullable=False), sa.Column("assinatura", _s(200)),
        sa.Column("sha256", _s(64), nullable=False), sa.Column("linhas", sa.Integer(), nullable=False),
        sa.Column("ultimo_mes", sa.Integer()),
        sa.Column("execucao_id", sa.Integer(), sa.ForeignKey("controle.execucoes.id")),
        sa.Column("carregado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("tipo", "ano", name="uq_audesp_arquivo_tipo_ano"),
        schema="controle", if_not_exists=True)
    comum = [sa.Column("id", sa.BigInteger(), primary_key=True), sa.Column("ano_arquivo", sa.Integer(), nullable=False),
             sa.Column("id_origem", _s(40), nullable=False)]
    op.create_table(
        "despesa", *comum,
        sa.Column("ano", sa.Integer(), nullable=False), sa.Column("mes", sa.Integer(), nullable=False),
        sa.Column("orgao", _s(200)), sa.Column("fase", _s(12), nullable=False), sa.Column("nr_empenho", _s(30)),
        sa.Column("dt_emissao", sa.Date()), sa.Column("credor_tipo", _s(10)), sa.Column("credor_cnpj", _s(14)),
        sa.Column("credor_codigo_tce", _s(30)), sa.Column("credor_nome", _s(300)), sa.Column("funcao", _s(120)),
        sa.Column("subfuncao", _s(160)), sa.Column("cd_programa", _s(10)), sa.Column("programa", _s(300)),
        sa.Column("cd_acao", _s(10)), sa.Column("acao", _s(300)), sa.Column("fonte_recurso", _s(160)),
        sa.Column("aplicacao_codigo", _s(4)), sa.Column("aplicacao", _s(300)), sa.Column("modalidade_licitacao", _s(60)),
        sa.Column("elemento_codigo", _s(8)), sa.Column("elemento", _s(300)), sa.Column("natureza", _s(15)),
        sa.Column("valor", V, nullable=False),
        schema="silver", if_not_exists=True)
    op.create_index("ix_silver_despesa_ano_mes", "despesa", ["ano", "mes"], schema="silver", if_not_exists=True)
    op.create_index("ix_silver_despesa_cnpj", "despesa", ["credor_cnpj"], schema="silver", if_not_exists=True)
    op.create_table(
        "receita", *[c.copy() for c in comum],
        sa.Column("ano", sa.Integer(), nullable=False), sa.Column("mes", sa.Integer(), nullable=False),
        sa.Column("orgao", _s(200)), sa.Column("poder", _s(30)), sa.Column("fonte_recurso", _s(160)),
        sa.Column("aplicacao_codigo", _s(4)), sa.Column("aplicacao", _s(300)), sa.Column("aplicacao_variavel", _s(300)),
        *[sa.Column(f"{n}{suf}", _s(8 if suf else 200)) for n in ("categoria", "subcategoria", "origem", "d1", "d2", "d3")
          for suf in ("_codigo", "")],
        sa.Column("natureza_codigo", _s(8)), sa.Column("natureza", _s(300)), sa.Column("valor", V, nullable=False),
        schema="silver", if_not_exists=True)
    op.create_index("ix_silver_receita_ano_mes", "receita", ["ano", "mes"], schema="silver", if_not_exists=True)
    op.create_table(
        "restos_a_pagar", *[c.copy() for c in comum],
        sa.Column("ano_balancete", sa.Integer(), nullable=False), sa.Column("mes_balancete", sa.Integer(), nullable=False),
        sa.Column("dt_referencia", sa.Date()), sa.Column("orgao", _s(200)), sa.Column("nr_empenho", _s(30)),
        sa.Column("ano_empenho", sa.Integer()), sa.Column("modalidade_licitacao", _s(60)), sa.Column("funcao", _s(160)),
        sa.Column("subfuncao", _s(160)), sa.Column("fonte_recurso", _s(160)), sa.Column("aplicacao_codigo", _s(4)),
        sa.Column("elemento_codigo", _s(8)), sa.Column("elemento", _s(300)), sa.Column("natureza", _s(15)),
        sa.Column("credor_tipo", _s(10)), sa.Column("credor_cnpj", _s(14)), sa.Column("credor_nome", _s(300)),
        *[sa.Column(n, V, nullable=False) for n in (
            "inscrito_proc", "saldo_inicial_proc", "cancelado_proc", "pago_proc", "estorno_pago_proc", "inscrito_nproc",
            "saldo_inicial_nproc", "liquidado_nproc", "estorno_liquidado_nproc", "cancelado_nproc", "pago_nproc",
            "estorno_pago_nproc")],
        schema="silver", if_not_exists=True)
    op.create_index("ix_silver_restos_ano_mes", "restos_a_pagar", ["ano_balancete", "mes_balancete"], schema="silver",
                    if_not_exists=True)
    op.execute("""
        INSERT INTO controle.agendamentos (conector, horario, ativo, ordem)
        VALUES ('audesp', '05:00', false, 8)
        ON CONFLICT (conector) DO NOTHING
    """)


def downgrade() -> None:
    pass  # sem exclusão física (padrão do projeto): tabelas e agendamento desligado são inofensivos
