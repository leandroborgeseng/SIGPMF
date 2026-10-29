"""Guarda reutilizável de conexão da origem SQL Server (R2 do #49).

`conecta_guardado`: autoriza o(s) banco(s) pela lista permitida ANTES de chamar o driver. Banco recusado ->
BaseRecusada e nenhuma conexão é aberta. Todo leitor novo usa esta função (o job de contagens do #51 vai usar).
A conferência de permissão efetiva da credencial fica em `app.core.permissoes_origem` (fora de app/connectors,
porque lista nomes de permissão que o teste de somente leitura do conector não aceita em literais).
"""
from collections.abc import Callable
from typing import Any

from app.connectors.sqlserver.politicas import autoriza_base


def conecta_guardado(conectar: Callable[..., Any], base: str, *outras_bases: str, **kwargs) -> Any:
    """Autoriza `base` (e bancos citados em consultas cross-database) e só então chama `conectar(database=base, ...)`."""
    for b in (base, *outras_bases):
        autoriza_base(b)
    return conectar(database=base, **kwargs)
