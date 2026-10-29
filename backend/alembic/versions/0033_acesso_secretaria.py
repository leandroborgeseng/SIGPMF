"""PR-06 (R1–R6): acesso por secretaria. Aditiva e idempotente: só cria tabelas novas no schema controle.

- controle.usuario_secretaria: vínculo usuário × secretaria com vigência, quem cadastrou e versão (conflito otimista).
  Nunca há DELETE físico: remover = `removido_em`/`removido_por`/`motivo_remocao` (o histórico vem da auditoria).
- controle.secretarias: tabela de referência (a TI ajusta sem deploy); seed EDUCACAO, SAUDE, FAZENDA, ASSISTENCIA_SOCIAL,
  ADMINISTRACAO, SERVICOS_URBANOS e TODAS (prefeito). Seed com ON CONFLICT DO NOTHING (não sobrescreve ajuste da TI).
- controle.dominio_secretaria: domínio do ouro -> secretaria dona, com `a_confirmar` (cemiterio -> SERVICOS_URBANOS a confirmar).
- controle.auditoria_acesso: uma linha por consulta nominal liberada pelo vínculo (R1/R3), somente inserção.

Numeração: 0033 reservada ao PR-06 pelo Tech Lead; down_revision '0030' (ponta da main bade7bb após #48); se outra migração entrar antes, aponta para a nova ponta
(no merge, o down_revision passa a apontar para a ponta da main).

Revision ID: 0033
Revises: 0030
"""
import sqlalchemy as sa
from alembic import op

revision = '0033'
down_revision = '0030'
branch_labels = None
depends_on = None


SECRETARIAS = [("EDUCACAO", "Secretaria de Educação"), ("SAUDE", "Secretaria de Saúde"), ("FAZENDA", "Secretaria da Fazenda"),
               ("ASSISTENCIA_SOCIAL", "Secretaria de Assistência Social"), ("ADMINISTRACAO", "Secretaria de Administração"),
               ("SERVICOS_URBANOS", "Secretaria de Serviços Urbanos"), ("TODAS", "Todas as secretarias (prefeito)")]
DOMINIOS = [("educacao", "EDUCACAO", False, None), ("saude", "SAUDE", False, None), ("tributario", "FAZENDA", False, None),
            ("impostos", "FAZENDA", False, None),
            ("cemiterio", "SERVICOS_URBANOS", True, "Valor inicial a confirmar pelo Tech Lead/Leandro"),
            ("qualidade_dados", "TODAS", False, "Pendências/inconsistências/óbito: prefeito; admin_lgpd por função com motivo (TL 29/10)"),
            ("explorador", "TODAS", False, "Explorador: prefeito; admin_lgpd por função com motivo (TL 29/10)"),
            ("sei", "ADMINISTRACAO", True, "Interessados do SEI: a confirmar")]


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if not insp.has_table("secretarias", schema="controle"):
        op.create_table(
            "secretarias",
            sa.Column("codigo", sa.String(40), primary_key=True),
            sa.Column("nome", sa.String(200), nullable=False),
            sa.Column("ativo", sa.Boolean, nullable=False, server_default="true"),
            sa.Column("a_confirmar", sa.Boolean, nullable=False, server_default="false"),
            sa.Column("obs", sa.Text),
            sa.Column("atualizado_por", sa.String(200)),
            sa.Column("atualizado_em", sa.DateTime(timezone=True), server_default=sa.func.now()),
            schema="controle",
        )
    if not insp.has_table("dominio_secretaria", schema="controle"):
        op.create_table(
            "dominio_secretaria",
            sa.Column("dominio", sa.String(50), primary_key=True),
            sa.Column("secretaria", sa.String(40), sa.ForeignKey("controle.secretarias.codigo"), nullable=False),
            sa.Column("a_confirmar", sa.Boolean, nullable=False, server_default="false"),
            sa.Column("obs", sa.Text),
            sa.Column("atualizado_por", sa.String(200)),
            sa.Column("atualizado_em", sa.DateTime(timezone=True), server_default=sa.func.now()),
            schema="controle",
        )
    b = op.get_bind()
    for c, n in SECRETARIAS:
        b.execute(sa.text("INSERT INTO controle.secretarias (codigo, nome) VALUES (:c, :n) ON CONFLICT (codigo) DO NOTHING"),
                  {"c": c, "n": n})
    for d, sec, ac, obs in DOMINIOS:
        b.execute(sa.text("INSERT INTO controle.dominio_secretaria (dominio, secretaria, a_confirmar, obs) "
                          "VALUES (:d, :s, :a, :o) ON CONFLICT (dominio) DO NOTHING"), {"d": d, "s": sec, "a": ac, "o": obs})
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
            sa.Column("revelou_cpf", sa.Boolean, nullable=False, server_default="false"),
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
    op.execute("DROP TABLE IF EXISTS controle.dominio_secretaria")
    op.execute("DROP TABLE IF EXISTS controle.secretarias")
