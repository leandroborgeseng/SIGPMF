"""PR-07 contagens automáticas (J1–J4). Aditiva: só cria tabelas novas em `controle`.

- contagem_execucao / contagem_resultado: só agregados (números e rótulos curtos), com hash do SQL e código fixo de falha.
- contagem_cpf_repetido (J4): só CPFs com ocorrências >= limiar (piso 50, CHECK no banco); leitura só por admin_lgpd,
  auditada. J4b: só a última execução guarda o CPF; as anteriores e as com mais de 30 dias ficam só com os totais
  (cpf = NULL por UPDATE, sem exclusão física).

down_revision = '0029' (ponta da main após #44 0027 e #47 0029).

Revision ID: 0034
Revises: 0029
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = '0034'
down_revision = '0029'
branch_labels = None
depends_on = None


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if not insp.has_table("contagem_execucao", schema="controle"):
        op.create_table(
            "contagem_execucao",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("usuario", sa.String(200)),
            sa.Column("status", sa.String(20), nullable=False),  # em_andamento | concluida | parcial | recusada
            sa.Column("codigo", sa.String(50)),
            sa.Column("limiar_cpf", sa.Integer, nullable=False),
            sa.Column("iniciado_em", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("terminado_em", sa.DateTime(timezone=True)),
            schema="controle",
        )
    if not insp.has_table("contagem_resultado", schema="controle"):
        op.create_table(
            "contagem_resultado",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("execucao_id", sa.Integer, sa.ForeignKey("controle.contagem_execucao.id"), nullable=False),
            sa.Column("consulta", sa.String(40), nullable=False),
            sa.Column("base", sa.String(100), nullable=False),
            sa.Column("status", sa.String(20), nullable=False),  # ok | falha
            sa.Column("codigo", sa.String(50)),
            sa.Column("sql_hash", sa.String(64), nullable=False),
            sa.Column("resultado", postgresql.JSONB),
            sa.Column("criado_em", sa.DateTime(timezone=True), server_default=sa.func.now()),
            schema="controle",
        )
        op.create_index("ix_contagem_resultado_execucao", "contagem_resultado", ["execucao_id"], schema="controle")
    if not insp.has_table("contagem_cpf_repetido", schema="controle"):
        op.create_table(
            "contagem_cpf_repetido",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("execucao_id", sa.Integer, sa.ForeignKey("controle.contagem_execucao.id"), nullable=False),
            sa.Column("base", sa.String(100), nullable=False),
            sa.Column("tabela", sa.String(200), nullable=False),
            sa.Column("cpf", sa.String(11)),  # J4b: NULL depois de expurgado (só a última execução guarda CPFs)
            sa.Column("ocorrencias", sa.Integer, nullable=False),
            sa.Column("limiar", sa.Integer, nullable=False),
            sa.Column("registrado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("expurgado_em", sa.DateTime(timezone=True)),
            sa.CheckConstraint("cpf IS NULL OR expurgado_em IS NULL", name="ck_contagem_cpf_repetido_expurgo"),
            sa.CheckConstraint("limiar >= 50 AND ocorrencias >= limiar", name="ck_contagem_cpf_repetido_limiar"),
            schema="controle",
        )


    # Agenda: 05:45 (fuso AGENDA_FUSO), depois das cargas da noite; nasce DESLIGADO, como os demais. Nunca altera linha existente.
    op.execute("""
        INSERT INTO controle.agendamentos (conector, horario, ativo, ordem)
        VALUES ('contagens', '05:45', false, 90)
        ON CONFLICT (conector) DO NOTHING
    """)


def downgrade() -> None:
    # sem exclusão física: o agendamento desligado `contagens` fica na tabela (mesma regra da 0024)
    op.execute("DROP TABLE IF EXISTS controle.contagem_cpf_repetido")
    op.execute("DROP TABLE IF EXISTS controle.contagem_resultado")
    op.execute("DROP TABLE IF EXISTS controle.contagem_execucao")
