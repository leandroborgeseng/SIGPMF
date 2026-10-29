"""Paginação start/limit do SEI (SEI4), mesma regra do SIL #27.

- start avança pelo nº de itens recebidos (não pelo limit).
- Fim: página vazia, ou página com menos itens que o limit.
- Página repetida (mesmos ids da anterior, ou nenhum id novo): para e marca PARCIAL com alerta; quem chamou não avança
  o checkpoint.
- Teto de páginas por execução: se a última lida veio cheia, a execução é PARCIAL (há mais) e o checkpoint não anda.
"""
from collections.abc import Callable
from typing import Any

from app.connectors.sei.cliente import ClienteSei, ErroSei


def lista(j: Any, chave: str) -> list[dict]:
    if j is None:
        return []
    if isinstance(j, list):
        return [x for x in j if isinstance(x, dict)]
    if isinstance(j, dict):
        v = j.get(chave)
        if v is None and isinstance(j.get("data"), dict):
            v = j["data"].get(chave)
        if isinstance(v, list):
            return [x for x in v if isinstance(x, dict)]
        if not j or v is None:
            return []
    raise ErroSei(f"resposta inesperada do SEI (sem '{chave}')")


def paginar(cliente: ClienteSei, rota: str, chave_lista: str, id_item: Callable[[dict], Any], limit: int,
            max_paginas: int, params: dict | None = None, protocolo: str | None = None) -> dict:
    start, paginas, itens, vistos = 0, 0, [], set()
    anterior: tuple | None = None
    fim, alerta = None, None
    while paginas < max_paginas:
        pagina = lista(cliente.get(rota, protocolo, {**(params or {}), "start": start, "limit": limit}), chave_lista)
        paginas += 1
        if not pagina:
            fim = "pagina_vazia"
            break
        ids = tuple(id_item(x) for x in pagina)
        novos = [x for x, i in zip(pagina, ids, strict=True) if i is not None and i not in vistos]
        if ids == anterior or not novos:
            fim, alerta = "pagina_repetida", {"motivo": "pagina_repetida", "start": start, "itens": len(pagina)}
            break
        for x, i in zip(pagina, ids, strict=True):
            if i is not None and i not in vistos:
                vistos.add(i)
                itens.append(x)
        anterior = ids
        start += len(pagina)
        if len(pagina) < limit:
            fim = "pagina_curta"
            break
    if fim is None:
        fim = "teto_paginas"
    parcial = fim in ("pagina_repetida", "teto_paginas")
    return {"itens": itens, "paginas": paginas, "fim": fim, "parcial": parcial, "alerta": alerta,
            "proximo_start": start}
