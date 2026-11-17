"""Cadastro físico de imóveis da SIL (CSV): tabela bronze nova. Aditiva.

A API não traz este cadastro. A carga (`scripts.carga_sil_imoveis`) faz upsert por idfisico.
Não apaga dado de outra tabela e não depende de SEED_ON_BOOT. O downgrade remove só esta tabela.

Revision ID: 0047
Revises: 0046
"""
from alembic import op

revision = "0047"
down_revision = "0046"
branch_labels = None
depends_on = None

_COMENTARIOS = {
    "idfisico": "Identificador do imóvel físico na SIL.",
    "inscrfisico": "Inscrição cadastral do imóvel físico.",
    "situacaofisico": "Situação do cadastro físico.",
    "crcresponsavel": "Código interno do responsável.",
    "nomeresponsavel": "Nome do responsável pelo imóvel.",
    "cpfcnpjresponsavel": "CPF ou CNPJ do responsável. Documento. Não entra no catálogo do chat.",
    "imovelcep": "CEP do endereço do imóvel.",
    "imoveltplogradouro": "Tipo de logradouro do endereço do imóvel.",
    "imovellogradouro": "Logradouro do endereço do imóvel.",
    "imovelendereco": "Endereço do imóvel.",
    "imovelbairro": "Bairro do endereço do imóvel.",
    "entregacep": "CEP do endereço de entrega.",
    "entregalogradouro": "Logradouro do endereço de entrega.",
    "entregaendereco": "Endereço de entrega.",
    "entregabairro": "Bairro do endereço de entrega.",
    "execucao_id": "Execução da carga que gravou a linha.",
    "carregado_em": "Momento da primeira gravação.",
    "atualizado_em": "Momento da última gravação.",
}


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS bronze.sil_imoveis_fisico (
            idfisico bigint PRIMARY KEY,
            inscrfisico text,
            situacaofisico text,
            crcresponsavel text,
            nomeresponsavel text,
            cpfcnpjresponsavel text,
            imovelcep text,
            imoveltplogradouro text,
            imovellogradouro text,
            imovelendereco text,
            imovelbairro text,
            entregacep text,
            entregalogradouro text,
            entregaendereco text,
            entregabairro text,
            execucao_id integer,
            carregado_em timestamptz NOT NULL DEFAULT now(),
            atualizado_em timestamptz NOT NULL DEFAULT now()
        )
    """)
    op.execute(
        "COMMENT ON TABLE bronze.sil_imoveis_fisico IS "
        "'Cadastro físico de imóveis da SIL (CSV, a API não traz). Inscrição, situação e endereço do imóvel.'"
    )
    for coluna, texto in _COMENTARIOS.items():
        op.execute(f"COMMENT ON COLUMN bronze.sil_imoveis_fisico.{coluna} IS '{texto}'")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS bronze.sil_imoveis_fisico")
