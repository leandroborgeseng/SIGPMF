"""NIS/PIS/PASEP (PR-01, N7): 11 dígitos com DV (pesos 3,2,9,8,7,6,5,4,3,2)."""
import re
from typing import NamedTuple

_NAO_DIGITO = re.compile(r"\D")
_PESOS = (3, 2, 9, 8, 7, 6, 5, 4, 3, 2)
MOTIVO_INVALIDO = "nis_invalido"


class Resultado(NamedTuple):
    valor: str | None
    motivo: str | None


def digito_verificador(base10: str) -> str:
    r = 11 - sum(int(c) * p for c, p in zip(base10, _PESOS, strict=True)) % 11
    return "0" if r >= 10 else str(r)


def normaliza(nis) -> Resultado:
    if nis is None or nis == "" or isinstance(nis, bool):
        return Resultado(None, None)
    d = str(nis).zfill(11) if isinstance(nis, int) else _NAO_DIGITO.sub("", str(nis))
    if len(d) != 11 or d == d[0] * 11 or digito_verificador(d[:10]) != d[10]:
        return Resultado(None, MOTIVO_INVALIDO)
    return Resultado(d, None)
