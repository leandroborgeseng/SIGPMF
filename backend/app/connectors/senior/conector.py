"""DBSENIOR usa o SqlServerConnector: conexao pelo guard e um unico SELECT por consulta.

Depois da leitura, cada linha ganha folha_nominal (Servidor ou Dependente) para o ouro.
Isso nao entra no SQL: nao e coluna da origem.
"""
from app.connectors.sqlserver.connector import SqlServerConnector

ROTULO = {"r034fun": "Servidor", "r036dep": "Dependente"}


def _marca(tabela: str, linhas: list) -> list:
    rotulo = ROTULO.get(str(tabela).split(".")[-1])
    if not rotulo:
        return linhas
    for linha in linhas:
        if isinstance(linha, dict):
            linha["folha_nominal"] = rotulo
    return linhas


class ConectorSenior(SqlServerConnector):
    """Mesma leitura do conector SQL Server, com o rotulo nominal acrescentado em memoria."""

    def ler_amostra(self, tabela, politica, campo_incremental=None, desde=None):
        return _marca(tabela, super().ler_amostra(tabela, politica, campo_incremental=campo_incremental, desde=desde))

    def ler_pagina(self, conn, tabela, politica, ordem, ultima, limite, desde=None, alinhada=False):
        return _marca(tabela, super().ler_pagina(
            conn, tabela, politica, ordem, ultima, limite, desde=desde, alinhada=alinhada))
