"""Inscrição imobiliária (PR-01, N6 e D8): só dígitos, 14 posições.

14 dígitos -> válido. 12 ou 13 -> lpad 14 com zeros (D8), motivo `inscricao_completada` (informativo, o valor vale).
Qualquer outro tamanho -> quarentena `inscricao_invalida`, sem valor.
"""
import re
from typing import NamedTuple

_NAO_DIGITO = re.compile(r"\D")
TAMANHO = 14
MOTIVO_COMPLETADA = "inscricao_completada"
MOTIVO_INVALIDA = "inscricao_invalida"


class Resultado(NamedTuple):
    valor: str | None
    motivo: str | None


def normaliza(inscricao) -> Resultado:
    if inscricao is None or inscricao == "" or isinstance(inscricao, bool):
        return Resultado(None, None)
    d = _NAO_DIGITO.sub("", str(inscricao))
    if not d:
        return Resultado(None, MOTIVO_INVALIDA)
    if len(d) == TAMANHO:
        return Resultado(d, None)
    if len(d) in (12, 13):
        return Resultado(d.zfill(TAMANHO), MOTIVO_COMPLETADA)
    return Resultado(None, MOTIVO_INVALIDA)
