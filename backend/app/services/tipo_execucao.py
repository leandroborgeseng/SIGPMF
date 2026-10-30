"""CT15/UXP9: tipo real da execução para a tela ("Amostra", "Incremental" ou "Carga completa"), tirado da própria
execução (tipo e modo), e o aviso de parada no teto de lotes (fim=max_lotes). Só contagens; nunca texto livre."""
from __future__ import annotations

import re

from app.models.controle import Execucao

AMOSTRA, INCREMENTAL, COMPLETA = "Amostra", "Incremental", "Carga completa"
_RE_FIM_MAX = re.compile(r"\bfim=max_lotes\b")
_RE_LOTES = re.compile(r"\blotes=(\d+)\b")
_RE_PARCIAL = re.compile(r"^parcial: (\d+) lote\(s\) lido\(s\)")


def _tipos_amostra() -> set[str]:
    from app.connectors.sil.carga import TIPO_EXECUCAO as SIL
    from app.services import carga_conector as cc
    from app.services import carga_gestaoescolar as cge
    # a amostra da SIL disparada pela tela grava "enriquecimento_sil" (ver carga_completa.TIPOS_AMOSTRA_EXTRA)
    return {*cc.TIPOS_AMOSTRA, cge.TIPO_AMOSTRA, SIL, "carga_demo_amostra"}


def tipo_texto(ex: Execucao) -> str:
    tipo = ex.tipo or ""
    if tipo in _tipos_amostra() or "amostra" in tipo:
        return AMOSTRA  # uma amostra nunca aparece como "Carga completa"
    return INCREMENTAL if ex.modo == "incremental" or "incremental" in tipo else COMPLETA


def lotes_no_limite(ex: Execucao) -> int | None:
    """Nº de lotes quando a execução parou no teto (fim=max_lotes ou amostra parcial da SIL); senão None."""
    msg = ex.mensagem or ""
    if _RE_FIM_MAX.search(msg):
        m = _RE_LOTES.search(msg)
        return int(m.group(1)) if m else max(1, int(ex.lotes or 1))
    m = _RE_PARCIAL.match(msg)
    return int(m.group(1)) if m else None


def _milhar(n: int) -> str:
    return f"{n:,}".replace(",", ".")


def texto_limite(ex: Execucao) -> str | None:
    n = lotes_no_limite(ex)
    if n is None:
        return None
    lidas = int(ex.lidos or 0)
    return (f"{tipo_texto(ex)}: {_milhar(lidas)} {'linha' if lidas == 1 else 'linhas'} "
            f"(parou no limite de {n} {'lote' if n == 1 else 'lotes'}). Não é o total da origem.")
