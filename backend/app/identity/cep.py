"""CEP (PR-01, N7): só dígitos e lpad 8 (ITBI.cep chega como int).

Menos de 5 dígitos -> nulo com motivo `cep_lixo`. Mais de 8 -> `cep_invalido`.
A existência no DNE (`valido_dne`) é do PR-02.
"""
import re
from typing import NamedTuple

_NAO_DIGITO = re.compile(r"\D")
MOTIVO_LIXO = "cep_lixo"
MOTIVO_INVALIDO = "cep_invalido"


class Resultado(NamedTuple):
    valor: str | None
    motivo: str | None


def normaliza(cep) -> Resultado:
    if cep is None or cep == "" or isinstance(cep, bool):
        return Resultado(None, None)
    d = _NAO_DIGITO.sub("", str(cep))
    if len(d) < 5 or int(d) == 0:
        return Resultado(None, MOTIVO_LIXO)
    if len(d) > 8:
        return Resultado(None, MOTIVO_INVALIDO)
    return Resultado(d.zfill(8), None)
