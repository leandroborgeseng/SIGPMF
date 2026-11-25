"""Fila de revisão dos interessados SEI sem CPF/CNPJ. Aditiva e idempotente.

O nome (sem documento) fica em controle, fora do catálogo do chat. Não apaga linha.
Reaplicar não recria a tabela.

Revision ID: 0052
Revises: 0051
"""
from alembic import op

revision = "0052"
down_revision = "0051"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS controle.sei_revisao_interessado (
            id serial PRIMARY KEY,
            id_procedimento varchar(50) NOT NULL,
            protocolo varchar(80),
            nome varchar(300),
            nome_norm varchar(300) NOT NULL,
            pendencia varchar(40) NOT NULL,
            ambiente_origem varchar(20) NOT NULL,
            atualizado_em timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_sei_revisao_interessado UNIQUE (id_procedimento, nome_norm, ambiente_origem)
        )""")


def downgrade() -> None:
    """Sem efeito: a fila de revisão é mantida (não apagar nome pendente)."""
    pass
