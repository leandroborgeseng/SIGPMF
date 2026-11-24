"""Cobertura de planos de saúde: beneficiários da ANS (dados abertos) e população do IBGE. Sem pessoa. Aditiva e idempotente.

Revision ID: 0043
Revises: 0042
"""
from alembic import op

revision = '0043'
down_revision = '0042'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""CREATE TABLE IF NOT EXISTS silver.ans_beneficiarios (
        ano INTEGER NOT NULL, mes INTEGER NOT NULL, faixa VARCHAR(20) NOT NULL, operadora VARCHAR(140) NOT NULL,
        contratacao VARCHAR(70) NOT NULL, cobertura VARCHAR(10) NOT NULL, ativos INTEGER NOT NULL, aderidos INTEGER NOT NULL, cancelados INTEGER NOT NULL,
        PRIMARY KEY (ano, mes, faixa, operadora, contratacao, cobertura))""")
    op.execute("""CREATE TABLE IF NOT EXISTS silver.ans_municipio (
        ano INTEGER NOT NULL, mes INTEGER NOT NULL, cd_municipio INTEGER NOT NULL, nome VARCHAR(60), medica INTEGER NOT NULL, odonto INTEGER NOT NULL,
        PRIMARY KEY (ano, mes, cd_municipio))""")
    op.execute("""CREATE TABLE IF NOT EXISTS controle.ans_meses (
        ano INTEGER NOT NULL, mes INTEGER NOT NULL, execucao_id INTEGER, carregado_em TIMESTAMPTZ NOT NULL DEFAULT now(), PRIMARY KEY (ano, mes))""")
    op.execute("""CREATE TABLE IF NOT EXISTS ref.ibge_populacao (
        cd_municipio INTEGER NOT NULL, ano INTEGER NOT NULL, populacao INTEGER NOT NULL, PRIMARY KEY (cd_municipio, ano))""")
    op.execute("""CREATE TABLE IF NOT EXISTS ref.ibge_faixa (
        cd_municipio INTEGER NOT NULL, faixa VARCHAR(20) NOT NULL, populacao INTEGER NOT NULL, PRIMARY KEY (cd_municipio, faixa))""")
    op.execute("""
        CREATE OR REPLACE FUNCTION gold.faixa_quinquenal(nascimento date) RETURNS text
        LANGUAGE sql STABLE PARALLEL SAFE AS $$
            SELECT CASE WHEN nascimento IS NULL THEN NULL
                WHEN extract(year FROM age(nascimento)) >= 80 THEN '80 anos ou mais'
                ELSE (floor(extract(year FROM age(nascimento)) / 5) * 5)::int || ' a ' || (floor(extract(year FROM age(nascimento)) / 5) * 5 + 4)::int || ' anos' END
        $$
    """)
    op.execute("""
        INSERT INTO controle.agendamentos (conector, horario, ativo, ordem)
        VALUES ('ans', '05:30', false, 10)
        ON CONFLICT (conector) DO NOTHING
    """)


def downgrade() -> None:
    pass  # sem exclusão física (padrão do projeto)
