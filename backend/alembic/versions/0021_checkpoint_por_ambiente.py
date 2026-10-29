"""Checkpoint da carga completa por (sistema, tabela, ambiente) (QA #30 A1).

Aditiva: coluna `ambiente` NOT NULL DEFAULT 'producao'. Os checkpoints SIL já existentes foram gravados sem
ambiente e podem ser da homologação (o "parado em 49"): são re-rotulados 'sandbox' (nada é apagado) e a
mudança fica na controle.auditoria. A primeira carga oficial começa em Sequencia=0.
Idempotente. Downgrade não faz nada (não apaga dado).

Revision ID: 0021
Revises: 0020
"""
import json

import sqlalchemy as sa
from alembic import op

revision = '0021'
down_revision = '0020'
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    cols = {c["name"] for c in sa.inspect(bind).get_columns("checkpoints_carga", schema="controle")}
    if "ambiente" in cols:
        return
    op.add_column("checkpoints_carga", sa.Column("ambiente", sa.String(20), nullable=False,
                                                 server_default="producao"), schema="controle")
    linhas = bind.execute(sa.text(
        "UPDATE controle.checkpoints_carga SET ambiente='sandbox' WHERE sistema='SIL' "
        "RETURNING id, tabela, posicao, ultima_chave")).all()
    if linhas:
        det = {"motivo": "checkpoint SIL legado sem ambiente; re-rotulado sandbox (QA #30 A1)",
               "checkpoints": [{"id": r.id, "tabela": r.tabela, "posicao": r.posicao,
                                "ultima_chave": r.ultima_chave} for r in linhas]}
        bind.execute(sa.text("INSERT INTO controle.auditoria (usuario, acao, alvo, detalhe) "
                             "VALUES ('migracao_0021', 'checkpoint_relabel', 'SIL', CAST(:d AS JSONB))"),
                     {"d": json.dumps(det, default=str)})
    op.drop_constraint("uq_checkpoint_sistema_tabela", "checkpoints_carga", schema="controle", type_="unique")
    op.create_unique_constraint("uq_checkpoint_sistema_tabela_ambiente", "checkpoints_carga",
                                ["sistema", "tabela", "ambiente"], schema="controle")


def downgrade() -> None:
    pass  # não apaga a coluna nem as marcas: um downgrade nunca perde dado
