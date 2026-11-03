"""Cubos do ouro: funções de normalização, estado dos cubos e agendamento DESLIGADO. Aditiva e idempotente.

As tabelas dos cubos não ficam aqui: nascem no primeiro recálculo (CREATE TABLE AS, app/gold/cubos.py).

Revision ID: 0041
Revises: 0040
"""
from alembic import op

revision = '0041'
down_revision = '0040'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(r"""
        CREATE OR REPLACE FUNCTION gold.norm_bairro(entrada text) RETURNS text
        LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE AS $$
        DECLARE s text;
        BEGIN
            IF entrada IS NULL THEN RETURN NULL; END IF;
            s = upper(translate(entrada, 'áàâãäéèêëíìîïóòôõöúùûüçÁÀÂÃÄÉÈÊËÍÌÎÏÓÒÔÕÖÚÙÛÜÇ', 'aaaaaeeeeiiiiooooouuuucAAAAAEEEEIIIIOOOOOUUUUC'));
            s = btrim(regexp_replace(regexp_replace(s, '[^A-Z0-9 ]', ' ', 'g'), '\s+', ' ', 'g'));
            s = regexp_replace(s, '^(JD|JDM|JDIM) ', 'JARDIM ');
            s = regexp_replace(s, '^(PQ|PRQ) ', 'PARQUE ');
            s = regexp_replace(s, '^(VL) ', 'VILA ');
            s = regexp_replace(s, '^(RES|RESID) ', 'RESIDENCIAL ');
            s = regexp_replace(s, '^(CH|CHAC) ', 'CHACARA ');
            s = regexp_replace(s, '^(CJ|CONJ) ', 'CONJUNTO ');
            IF s IN ('', '0', 'NI', 'N I', 'NAO INFORMADO', 'NAO CONSTA', 'SEM BAIRRO', 'NAO IDENTIFICADO') THEN RETURN NULL; END IF;
            RETURN s;
        END $$
    """)
    op.execute("""
        CREATE OR REPLACE FUNCTION gold.faixa_etaria(nascimento date) RETURNS text
        LANGUAGE sql STABLE PARALLEL SAFE AS $$
            SELECT CASE WHEN nascimento IS NULL THEN 'não informada'
                WHEN extract(year FROM age(nascimento)) < 6 THEN '0-5'
                WHEN extract(year FROM age(nascimento)) < 15 THEN '6-14'
                WHEN extract(year FROM age(nascimento)) < 18 THEN '15-17'
                WHEN extract(year FROM age(nascimento)) < 30 THEN '18-29'
                WHEN extract(year FROM age(nascimento)) < 60 THEN '30-59'
                ELSE '60+' END
        $$
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS gold.cubos_estado (
            cubo varchar(40) PRIMARY KEY, linhas bigint NOT NULL, segundos numeric(10,2) NOT NULL,
            gerado_em timestamptz NOT NULL DEFAULT now())
    """)
    op.execute("""
        INSERT INTO controle.agendamentos (conector, horario, ativo, ordem)
        VALUES ('cubos', '05:50', false, 95)
        ON CONFLICT (conector) DO NOTHING
    """)


def downgrade() -> None:
    pass  # sem exclusão física (padrão do projeto)
