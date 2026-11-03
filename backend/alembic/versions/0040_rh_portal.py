"""RH pelo Portal da Transparência (RH1): silver.rh_remuneracao, controle.rh_meses e agendamento DESLIGADO. Aditiva.

Revision ID: 0040
Revises: 0039
"""
import sqlalchemy as sa
from alembic import op

revision = '0040'
down_revision = '0039'
branch_labels = None
depends_on = None

V = sa.Numeric(14, 2)


def upgrade() -> None:
    op.create_table(
        "rh_meses",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ano", sa.Integer(), nullable=False), sa.Column("mes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(64)), sa.Column("linhas", sa.Integer(), nullable=False),
        sa.Column("execucao_id", sa.Integer(), sa.ForeignKey("controle.execucoes.id")),
        sa.Column("carregado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("ano", "mes", name="uq_rh_mes"),
        schema="controle", if_not_exists=True)
    op.create_table(
        "rh_remuneracao",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("ano", sa.Integer(), nullable=False), sa.Column("mes", sa.Integer(), nullable=False),
        sa.Column("matricula", sa.String(20), nullable=False), sa.Column("nome", sa.String(200)),
        sa.Column("situacao", sa.String(30)), sa.Column("cargo", sa.String(120)), sa.Column("funcao_inativa", sa.String(120)),
        sa.Column("secretaria", sa.String(80), nullable=False), sa.Column("secretaria_origem", sa.String(120)),
        sa.Column("grupo", sa.String(12), nullable=False), sa.Column("jornada", sa.String(10)), sa.Column("referencia", sa.String(20)),
        sa.Column("salario_base", V, nullable=False), sa.Column("vencimentos", V, nullable=False),
        sa.Column("descontos", V, nullable=False), sa.Column("liquido", V, nullable=False),
        sa.Column("admissao", sa.Date()), sa.Column("afastamento", sa.Date()), sa.Column("demissao", sa.Date()),
        schema="silver", if_not_exists=True)
    op.create_index("ix_silver_rh_ano_mes", "rh_remuneracao", ["ano", "mes"], schema="silver", if_not_exists=True)
    op.create_index("ix_silver_rh_matricula", "rh_remuneracao", ["matricula"], schema="silver", if_not_exists=True)
    op.create_index("ix_silver_rh_secretaria", "rh_remuneracao", ["secretaria"], schema="silver", if_not_exists=True)
    op.execute("""
        INSERT INTO controle.agendamentos (conector, horario, ativo, ordem)
        VALUES ('rh_portal', '05:20', false, 9)
        ON CONFLICT (conector) DO NOTHING
    """)


def downgrade() -> None:
    pass  # sem exclusão física (padrão do projeto)
