"""SEI M4/reclassificação: marcador mínimo de processo não público e tentativas por processo pai desconhecido.

Aditiva e idempotente (CREATE TABLE IF NOT EXISTS). O marcador guarda só idProcesso, protocolo (só dígitos) e nível
de acesso: sem conteúdo, assunto, unidade nem interessados. Downgrade não faz nada (não apaga dado).

Revision ID: 0023
Revises: 0022
"""
from alembic import op

revision = '0023'
down_revision = '0022'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS bronze.sei_processos_nao_publicos (
            id serial PRIMARY KEY,
            id_processo varchar(50) NOT NULL,
            protocolo_digitos varchar(50),
            nivel_acesso varchar(5) NOT NULL,
            ambiente_origem varchar(20) NOT NULL,
            atualizado_em timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_sei_nao_publico UNIQUE (id_processo, ambiente_origem)
        )""")
    op.execute("""
        CREATE TABLE IF NOT EXISTS controle.sei_pais_pendentes (
            id serial PRIMARY KEY,
            id_processo varchar(50) NOT NULL,
            ambiente_origem varchar(20) NOT NULL,
            tentativas integer NOT NULL DEFAULT 0,
            situacao varchar(20) NOT NULL DEFAULT 'pendente',
            execucao_id integer REFERENCES controle.execucoes(id),
            atualizado_em timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_sei_pai_pendente UNIQUE (id_processo, ambiente_origem)
        )""")


def downgrade() -> None:
    """Sem efeito: marcadores e tentativas são mantidos (não apagar dados de procedência)."""
    pass
