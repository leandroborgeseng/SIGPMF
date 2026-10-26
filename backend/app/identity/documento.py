"""Documento (CPF/CNPJ) do Munícipe: normalização, validação e máscara (aceite M2/M7).

Toda comparação usa só dígitos (trim, sem . - / e espaços); vazio vira nulo.
"""
import re

from app.identity import cpf as cpfmod

_NAO_DIGITO = re.compile(r"\D")


def normaliza(doc: str | None) -> str | None:
    if doc is None:
        return None
    d = _NAO_DIGITO.sub("", str(doc))
    return d or None


def cnpj_valido(doc: str | None) -> bool:
    d = normaliza(doc)
    if not d or len(d) != 14 or d == d[0] * 14:
        return False

    def dv(parcial: str) -> str:
        pesos = list(range(len(parcial) - 7, 1, -1)) + list(range(9, 1, -1))
        r = sum(int(c) * p for c, p in zip(parcial, pesos, strict=True)) % 11
        return "0" if r < 2 else str(11 - r)
    d1 = dv(d[:12])
    return d1 + dv(d[:12] + d1) == d[12:]


def valido(doc: str | None, tipo: str | None) -> bool:
    """F -> CPF (11 dígitos); J -> CNPJ (14 dígitos). Outros tipos não validam."""
    if tipo == "F":
        return cpfmod.valido(doc)
    if tipo == "J":
        return cnpj_valido(doc)
    return False


def mascara(doc: str | None) -> str | None:
    d = normaliza(doc)
    if not d:
        return None
    if len(d) == 14:
        return f"**.{d[2:5]}.***/****-**"
    return cpfmod.mascara(d)
