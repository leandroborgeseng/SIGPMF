"""PR-06 (R1–R6): acesso por secretaria. Aditiva e idempotente: só cria tabelas novas no schema controle.

- controle.usuario_secretaria: vínculo usuário × secretaria com vigência, quem cadastrou e versão (conflito otimista).
  Nunca há DELETE físico: remover = `removido_em`/`removido_por`/`motivo_remocao` (o histórico vem da auditoria).
- controle.auditoria_acesso: uma linha por consulta nominal liberada pelo vínculo (R1/R3), somente inserção.

Numeração: 0033 reservada ao PR-06 pelo Tech Lead; down_revision provisório '0026' até os PRs 0027–0032 entrarem na main
(no merge, o down_revision passa a apontar para a ponta da main).

Revision ID: 0033
Revises: 0026
"""
import sqlalchemy as sa
from alembic import op

revision = '0033'
down_revision = '0026'
branch_labels = None
depends_on = None


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if not insp.has_table("usuario_secretaria", schema="controle"):
        op.create_table(
            "usuario_secretaria",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("usuario_id", sa.Integer, sa.ForeignKey("controle.usuarios.id"), nullable=False),
            sa.Column("secretaria", sa.String(40), nullable=False),
            sa.Column("perfil", sa.String(20)),
            sa.Column("vigencia_inicio", sa.Date, nullable=False),
            sa.Column("vigencia_fim", sa.Date),
            sa.Column("cadastrado_por", sa.String(200), nullable=False),
            sa.Column("cadastrado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("atualizado_por", sa.String(200)),
            sa.Column("atualizado_em", sa.DateTime(timezone=True)),
            sa.Column("removido_por", sa.String(200)),
            sa.Column("removido_em", sa.DateTime(timezone=True)),
            sa.Column("motivo_remocao", sa.Text),
            sa.Column("versao", sa.Integer, nullable=False, server_default="1"),
            sa.CheckConstraint("vigencia_fim IS NULL OR vigencia_fim >= vigencia_inicio", name="ck_usuario_secretaria_vigencia"),
            schema="controle",
        )
        op.create_index("uq_usuario_secretaria_ativo", "usuario_secretaria", ["usuario_id", "secretaria"], unique=True,
                        schema="controle", postgresql_where=sa.text("removido_em IS NULL"))
    if not insp.has_table("auditoria_acesso", schema="controle"):
        op.create_table(
            "auditoria_acesso",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("usuario", sa.String(200), nullable=False),
            sa.Column("secretaria", sa.String(40), nullable=False),
            sa.Column("recurso", sa.String(50), nullable=False),
            sa.Column("alvo", sa.String(300)),
            sa.Column("ip", sa.String(64)),
            sa.Column("criado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            schema="controle",
        )
        op.execute("""
            CREATE OR REPLACE FUNCTION controle.auditoria_acesso_imutavel() RETURNS trigger AS $$
            BEGIN RAISE EXCEPTION 'controle.auditoria_acesso é somente inserção'; END; $$ LANGUAGE plpgsql;
            CREATE TRIGGER trg_auditoria_acesso_imutavel BEFORE UPDATE OR DELETE ON controle.auditoria_acesso
            FOR EACH ROW EXECUTE FUNCTION controle.auditoria_acesso_imutavel();
            CREATE TRIGGER trg_auditoria_acesso_sem_truncate BEFORE TRUNCATE ON controle.auditoria_acesso
            FOR EACH STATEMENT EXECUTE FUNCTION controle.auditoria_acesso_imutavel();
            REVOKE UPDATE, DELETE, TRUNCATE ON controle.auditoria_acesso FROM PUBLIC;
        """)


def downgrade() -> None:
    # Remove só o que esta migração criou (tabelas novas); nenhum dado anterior é tocado.
    op.execute("DROP TABLE IF EXISTS controle.auditoria_acesso")
    op.execute("DROP FUNCTION IF EXISTS controle.auditoria_acesso_imutavel()")
    op.execute("DROP TABLE IF EXISTS controle.usuario_secretaria")
