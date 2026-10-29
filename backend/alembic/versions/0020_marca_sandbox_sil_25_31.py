"""Dados: marca ambiente_origem='sandbox' SÓ na bronze SIL das execuções 25 e 31 (amostras na homologação).

Conta antes e aborta (sem mudar nada) se o total não bater com novos+atualizados dessas execuções ou passar de 98.
Idempotente, sem DELETE. Downgrade não desfaz nada (não destrutivo).

Revision ID: 0020
Revises: 0019
"""
from alembic import op

revision = '0020'
down_revision = '0019'
branch_labels = None
depends_on = None


def upgrade() -> None:
    from app.services.marca_sandbox_sil import marcar
    marcar(op.get_bind())


def downgrade() -> None:
    pass  # não restaura nem apaga nada
