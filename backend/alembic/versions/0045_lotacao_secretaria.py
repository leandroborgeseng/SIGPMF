"""De-para de lotação (FL5/FL8/FL9): ref.lotacao_secretaria, só ESTRUTURA do organograma 7 (códigos e nomes, sem pessoa).

- secretaria = 2 primeiros segmentos do codloc (`1.x`); ranking = 1.1–1.11 e 1.16;
- 1.12–1.15 = grupo "outros_vinculos" (rótulo "Outros vínculos"), fora do ranking e da média da prefeitura;
- raiz `1` = "sem_lotacao" ("Sem lotação"), entra no total.

Aditiva e idempotente: CREATE IF NOT EXISTS + INSERT ON CONFLICT DO NOTHING. Não carrega dado real.

Revision ID: 0045
Revises: 0044
"""
from alembic import op

revision = '0045'
down_revision = '0044'
branch_labels = None
depends_on = None

ESTRUTURA = (
    ("1", "Sem lotação", "sem_lotacao", 99),
    ("1.1", "Gabinete do Prefeito", "secretaria", 1),
    ("1.2", "Procuradoria Geral", "secretaria", 2),
    ("1.3", "Ação Social", "secretaria", 3),
    ("1.4", "Administração e RH", "secretaria", 4),
    ("1.5", "Inovação e Desenvolvimento", "secretaria", 5),
    ("1.6", "Educação", "secretaria", 6),
    ("1.7", "Finanças", "secretaria", 7),
    ("1.8", "Infraestrutura", "secretaria", 8),
    ("1.9", "Meio Ambiente", "secretaria", 9),
    ("1.10", "Saúde", "secretaria", 10),
    ("1.11", "Segurança", "secretaria", 11),
    ("1.12", "Cedidos", "outros_vinculos", 51),
    ("1.13", "Aposentados", "outros_vinculos", 52),
    ("1.14", "Pensionistas", "outros_vinculos", 53),
    ("1.15", "Indenizados", "outros_vinculos", 54),
    ("1.16", "Esporte e Cultura", "secretaria", 12),
)


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS ref")
    op.execute("""CREATE TABLE IF NOT EXISTS ref.lotacao_secretaria (
        codloc_secretaria VARCHAR(20) PRIMARY KEY,
        nome VARCHAR(120) NOT NULL,
        grupo VARCHAR(20) NOT NULL CHECK (grupo IN ('secretaria', 'outros_vinculos', 'sem_lotacao')),
        ordem INTEGER NOT NULL)""")
    for cod, nome, grupo, ordem in ESTRUTURA:
        op.execute(f"INSERT INTO ref.lotacao_secretaria (codloc_secretaria, nome, grupo, ordem) "
                   f"VALUES ('{cod}', '{nome}', '{grupo}', {ordem}) ON CONFLICT (codloc_secretaria) DO NOTHING")


def downgrade() -> None:
    # Aditiva (convenção do repo, ver 0044): o downgrade não apaga a tabela nem o de-para.
    pass
