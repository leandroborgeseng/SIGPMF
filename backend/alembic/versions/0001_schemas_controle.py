"""schemas bronze/silver/gold/controle e tabelas de controle

Revision ID: 0001
Revises:
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

SCHEMAS = ("bronze", "silver", "gold", "controle")
TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    for s in SCHEMAS:
        op.execute(f"CREATE SCHEMA IF NOT EXISTS {s}")
    op.create_table(
        "sistemas",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("codigo", sa.String(50), nullable=False, unique=True),
        sa.Column("nome", sa.String(200), nullable=False),
        sa.Column("descricao", sa.Text),
        sa.Column("dominio", sa.String(50)),
        sa.Column("ativo", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("criado_em", TS, nullable=False, server_default=sa.func.now()),
        schema="controle")
    op.create_table(
        "tabelas_catalogo",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("sistema_id", sa.Integer, sa.ForeignKey("controle.sistemas.id", ondelete="CASCADE"), nullable=False),
        sa.Column("nome", sa.String(200), nullable=False),
        sa.Column("chave", sa.String(200), nullable=False),
        sa.Column("campo_incremental", sa.String(200)),
        sa.Column("ativo", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("criado_em", TS, nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("sistema_id", "nome"),
        schema="controle")
    op.create_table(
        "execucoes",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("tipo", sa.String(50), nullable=False),
        sa.Column("sistema", sa.String(50)),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("usuario", sa.String(200)),
        sa.Column("iniciado_em", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("finalizado_em", TS),
        sa.Column("lidos", sa.Integer, nullable=False, server_default="0"),
        sa.Column("novos", sa.Integer, nullable=False, server_default="0"),
        sa.Column("atualizados", sa.Integer, nullable=False, server_default="0"),
        sa.Column("ignorados", sa.Integer, nullable=False, server_default="0"),
        sa.Column("lotes", sa.Integer, nullable=False, server_default="0"),
        sa.Column("mensagem", sa.Text),
        schema="controle")
    op.create_table(
        "logs",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("execucao_id", sa.Integer, sa.ForeignKey("controle.execucoes.id")),
        sa.Column("nivel", sa.String(10), nullable=False),
        sa.Column("mensagem", sa.Text, nullable=False),
        sa.Column("criado_em", TS, nullable=False, server_default=sa.func.now()),
        schema="controle")
    op.create_table(
        "usuarios",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("email", sa.String(200), nullable=False, unique=True),
        sa.Column("nome", sa.String(200), nullable=False),
        sa.Column("perfil", sa.String(20), nullable=False),
        sa.Column("senha_hash", sa.String(200), nullable=False),
        sa.Column("ativo", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("criado_em", TS, nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("perfil in ('admin','admin_lgpd','operador','leitura')", name="ck_usuarios_perfil"),
        schema="controle")
    op.create_table(
        "auditoria",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("usuario", sa.String(200)),
        sa.Column("acao", sa.String(50), nullable=False),
        sa.Column("alvo", sa.String(300)),
        sa.Column("detalhe", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("ip", sa.String(64)),
        sa.Column("criado_em", TS, nullable=False, server_default=sa.func.now()),
        schema="controle")
    op.create_table(
        "parametros",
        sa.Column("chave", sa.String(100), primary_key=True),
        sa.Column("valor", sa.String(200), nullable=False),
        sa.Column("descricao", sa.Text),
        schema="controle")
    op.execute("""INSERT INTO controle.parametros (chave, valor, descricao) VALUES
        ('correlacao_limiar_revisao', '0.85', 'Score mínimo (sem CPF) para mandar à revisão; abaixo cria novo cidadao_id'),
        ('correlacao_peso_nome', '0.40', 'Peso do nome normalizado'),
        ('correlacao_peso_nascimento', '0.30', 'Peso da data de nascimento'),
        ('correlacao_peso_mae', '0.25', 'Peso do nome da mãe')""")
    # Auditoria é append-only: o próprio banco recusa UPDATE e DELETE.
    op.execute("""
        CREATE OR REPLACE FUNCTION controle.auditoria_imutavel() RETURNS trigger AS $$
        BEGIN RAISE EXCEPTION 'controle.auditoria é somente inserção'; END; $$ LANGUAGE plpgsql;
        CREATE TRIGGER trg_auditoria_imutavel BEFORE UPDATE OR DELETE ON controle.auditoria
        FOR EACH ROW EXECUTE FUNCTION controle.auditoria_imutavel();
        CREATE TRIGGER trg_auditoria_sem_truncate BEFORE TRUNCATE ON controle.auditoria
        FOR EACH STATEMENT EXECUTE FUNCTION controle.auditoria_imutavel();
        REVOKE UPDATE, DELETE, TRUNCATE ON controle.auditoria FROM PUBLIC;
    """)


def downgrade() -> None:
    for t in ("parametros", "auditoria", "usuarios", "logs", "execucoes", "tabelas_catalogo", "sistemas"):
        op.drop_table(t, schema="controle")
    op.execute("DROP FUNCTION IF EXISTS controle.auditoria_imutavel()")
    for s in ("bronze", "silver", "gold"):
        op.execute(f"DROP SCHEMA IF EXISTS {s} CASCADE")
