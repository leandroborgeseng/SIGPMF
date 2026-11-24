"""Dicionário da natureza da dívida (ref.divida_natureza, por CSV) e norm_bairro sem textos que não são bairro. Aditiva e idempotente.

Revision ID: 0042
Revises: 0041
"""
from alembic import op

revision = '0042'
down_revision = '0041'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""CREATE TABLE IF NOT EXISTS ref.divida_natureza (
        codigo INTEGER PRIMARY KEY, descricao VARCHAR(200) NOT NULL, carregado_em TIMESTAMPTZ NOT NULL DEFAULT now())""")
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
            IF s IN ('', '0', 'NI', 'N I', 'NAO INFORMADO', 'NAO CONSTA', 'SEM BAIRRO', 'NAO IDENTIFICADO', 'CODIGO DO CADASTRO ANTIGO')
               OR s LIKE 'BAIRRO CADASTRADO NA ORIGEM%' THEN RETURN NULL; END IF;
            RETURN s;
        END $$
    """)


def downgrade() -> None:
    pass  # sem exclusão física (padrão do projeto)
