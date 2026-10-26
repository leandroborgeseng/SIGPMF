"""Cartão Nacional de Saúde (CNS): validação oficial, tipo e máscara.

- 15 dígitos. Começa com 1 ou 2: definitivo (cálculo a partir do PIS, 11 primeiros dígitos).
- Começa com 7, 8 ou 9: provisório (soma ponderada por 15..1 divisível por 11).
"""
import re

_NAO_DIGITO = re.compile(r"\D")


def normaliza(cns: str | None) -> str | None:
    if cns is None or cns == "" or cns is False:
        return None
    if isinstance(cns, int) and not isinstance(cns, bool):  # numérico na origem perde zeros à esquerda
        return str(cns).zfill(15) if cns > 0 else None
    d = _NAO_DIGITO.sub("", str(cns))
    return d or None


def _definitivo_esperado(pis: str) -> str:
    soma = sum(int(pis[i]) * (15 - i) for i in range(11))
    dv = 11 - soma % 11
    if dv == 11:
        dv = 0
    if dv == 10:
        soma += 2
        dv = 11 - soma % 11
        return f"{pis}001{dv}"
    return f"{pis}000{dv}"


def tipo(cns: str | None) -> str | None:
    """'definitivo', 'provisorio' ou None (inválido)."""
    d = normaliza(cns)
    if not d or len(d) != 15:
        return None
    if d[0] in "12":
        return "definitivo" if _definitivo_esperado(d[:11]) == d else None
    if d[0] in "789":
        return "provisorio" if sum(int(c) * (15 - i) for i, c in enumerate(d)) % 11 == 0 else None
    return None


def valido(cns: str | None) -> bool:
    return tipo(cns) is not None


def mascara(cns: str | None) -> str | None:
    d = normaliza(cns)
    if not d:
        return None
    return f"*** {d[3:7]} {d[7:11]} ****" if len(d) == 15 else "*** **** **** ****"


def gera_definitivo(pis11: str) -> str:
    return _definitivo_esperado(pis11)


def gera_provisorio(prefixo14: str) -> str | None:
    """Completa 14 dígitos (começando com 7/8/9) com o 15º que torna a soma divisível por 11; None se impossível."""
    s = sum(int(c) * (15 - i) for i, c in enumerate(prefixo14))
    d = (-s) % 11
    return None if d == 10 else prefixo14 + str(d)
