"""PR-03 modelo prata (S1–S4): `vinculos` generalizado, `empresa`, `cidadao_empresa` e procedência por valor.

Aditiva: só ADD COLUMN com default (as linhas atuais de `silver.vinculos` continuam as mesmas, sem UPDATE em massa
explícito nem DELETE) e CREATE TABLE. Idempotente (reaplicável a partir de um stamp anterior).

down_revision = '0034' (ponta da main em 44c08e3, depois do #47 e do #51), com uma ponta só.

Revision ID: 0030
Revises: 0034
"""
import sqlalchemy as sa
from alembic import op

revision = '0030'
down_revision = '0034'
branch_labels = None
depends_on = None

# (nome, tipo, server_default) — colunas novas em silver.vinculos
COLUNAS_VINCULO = (
    ("entidade", sa.String(20), "cidadao"),           # cidadao | empresa | imovel | endereco
    ("tipo_chave", sa.String(30), None),              # cpf | cnpj | inscricao | ccm | chave_origem ...
    ("chave_hash", sa.String(64), None),              # sha256 da chave normalizada (nunca a chave crua)
    ("status", sa.String(20), "confirmado"),          # confirmado | revisao | quarentena
    ("ambiente_origem", sa.String(20), "producao"),
)


def _tem_coluna(insp, tabela, coluna, schema="silver") -> bool:
    return any(c["name"] == coluna for c in insp.get_columns(tabela, schema=schema))


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    for nome, tipo, default in COLUNAS_VINCULO:
        if not _tem_coluna(insp, "vinculos", nome):
            op.add_column("vinculos", sa.Column(nome, tipo, nullable=default is None,
                                                server_default=default), schema="silver")
    if not any(c["name"] == "ck_silver_vinculos_status" for c in insp.get_check_constraints("vinculos", schema="silver")):
        op.create_check_constraint("ck_silver_vinculos_status", "vinculos",
                                   "status IN ('confirmado','revisao','quarentena')", schema="silver")

    if not insp.has_table("empresa", schema="silver"):
        op.create_table(
            "empresa",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("cnpj", sa.String(14)),                 # só dígitos; UNIQUE quando presente
            sa.Column("fonte_inscricao", sa.String(50)),      # sistema dono da inscrição municipal (SIL, ISS...)
            sa.Column("inscricao_municipal", sa.String(30)),
            sa.Column("razao_social", sa.String(300)),
            sa.Column("criado_em", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.CheckConstraint("cnpj IS NOT NULL OR inscricao_municipal IS NOT NULL", name="ck_silver_empresa_chave"),
            sa.UniqueConstraint("fonte_inscricao", "inscricao_municipal", name="uq_silver_empresa_fonte_inscricao"),
            schema="silver",
        )
        op.create_index("uq_silver_empresa_cnpj", "empresa", ["cnpj"], unique=True, schema="silver",
                        postgresql_where=sa.text("cnpj IS NOT NULL"))
    if not insp.has_table("cidadao_empresa", schema="silver"):
        op.create_table(
            "cidadao_empresa",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("cidadao_id", sa.String(36),
                      sa.ForeignKey("silver.cidadaos.cidadao_id", ondelete="RESTRICT"), nullable=False),
            sa.Column("empresa_id", sa.Integer, sa.ForeignKey("silver.empresa.id", ondelete="RESTRICT"),
                      nullable=False),
            sa.Column("papel", sa.String(30), nullable=False),  # mei | responsavel | socio
            sa.Column("sistema", sa.String(50), nullable=False),
            sa.Column("tabela", sa.String(200), nullable=False),
            sa.Column("chave_hash", sa.String(64), nullable=False),
            sa.Column("criado_em", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.UniqueConstraint("cidadao_id", "empresa_id", "papel", name="uq_silver_cidadao_empresa"),
            schema="silver",
        )
        op.create_index("ix_silver_cidadao_empresa_empresa", "cidadao_empresa", ["empresa_id"], schema="silver")
    if not insp.has_table("procedencia_valor", schema="silver"):
        op.create_table(
            "procedencia_valor",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("entidade", sa.String(20), nullable=False),
            sa.Column("entidade_id", sa.String(36), nullable=False),
            sa.Column("campo", sa.String(60), nullable=False),
            sa.Column("valor", sa.Text),
            sa.Column("completude", sa.Integer, nullable=False, server_default="0"),
            sa.Column("sistema", sa.String(50), nullable=False),
            sa.Column("tabela", sa.String(200), nullable=False),
            sa.Column("chave_hash", sa.String(64), nullable=False),
            sa.Column("data_referencia", sa.DateTime(timezone=True)),
            sa.Column("vencedor", sa.Boolean, nullable=False, server_default="false"),
            sa.Column("registrado_em", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.UniqueConstraint("entidade", "entidade_id", "campo", "sistema", "tabela", "chave_hash",
                                name="uq_silver_procedencia_origem"),
            schema="silver",
        )
        op.create_index("ix_silver_procedencia_entidade", "procedencia_valor", ["entidade", "entidade_id", "campo"],
                        schema="silver")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS silver.procedencia_valor")
    op.execute("DROP TABLE IF EXISTS silver.cidadao_empresa")
    op.execute("DROP TABLE IF EXISTS silver.empresa")
    op.execute("ALTER TABLE silver.vinculos DROP CONSTRAINT IF EXISTS ck_silver_vinculos_status")
    for nome, _t, _d in reversed(COLUNAS_VINCULO):
        op.execute(f"ALTER TABLE silver.vinculos DROP COLUMN IF EXISTS {nome}")
