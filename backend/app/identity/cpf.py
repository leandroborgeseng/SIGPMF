"""Validação, normalização e máscara de CPF.

PR-01 (N1-N4, N3a): `classifica` devolve (valor, motivo) e é a porta única antes do `uuid5`.
A lista de números de preenchimento vem da configuração (CPF_PREENCHIMENTO), nunca do código.
"""
import re
from typing import NamedTuple

_NAO_DIGITO = re.compile(r"\D")
_NUMERICO = re.compile(r"^\s*\d{1,11}(\.0+)?\s*$")

MOTIVO_CPF_INVALIDO = "cpf_invalido"


class Resultado(NamedTuple):
    valor: str | None
    motivo: str | None  # None = válido


def normaliza(cpf: str | None, origem_numerica: bool = False) -> str | None:
    """Só dígitos. int (ou string numérica com origem_numerica=True) recebe zfill(11)."""
    if cpf is None or cpf == "" or cpf is False:
        return None
    if isinstance(cpf, int) and not isinstance(cpf, bool):  # numérico na origem perde zeros à esquerda
        return str(cpf).zfill(11) if cpf > 0 else None
    if origem_numerica and _NUMERICO.match(str(cpf)):  # bigint/numeric que chega como "1234567890" ou "1234567890.0"
        n = int(str(cpf).strip().split(".")[0])
        return str(n).zfill(11) if n > 0 else None
    d = _NAO_DIGITO.sub("", str(cpf))
    return d or None


def digitos_verificadores(base9: str) -> str:
    def dv(parcial: str) -> str:
        peso = len(parcial) + 1
        soma = sum(int(c) * (peso - i) for i, c in enumerate(parcial))
        r = (soma * 10) % 11
        return "0" if r == 10 else str(r)
    d1 = dv(base9)
    return d1 + dv(base9 + d1)


def valido(cpf: str | None) -> bool:
    d = normaliza(cpf)
    if not d or len(d) != 11 or d == d[0] * 11:
        return False
    return digitos_verificadores(d[:9]) == d[9:]


def mascara(cpf: str | None) -> str | None:
    """***.456.789-** : expõe só os dígitos 4 a 9."""
    d = normaliza(cpf)
    if not d:
        return None
    if len(d) != 11:
        return "***.***.***-**"
    return f"***.{d[3:6]}.{d[6:9]}-**"


def mascara_dict(dados: dict) -> dict:
    """Copia de um registro com qualquer campo de CPF mascarado."""
    out = {}
    for k, v in dados.items():
        out[k] = mascara(v) if "cpf" in k.lower() and isinstance(v, str) else v
    return out


def lista_preenchimento() -> frozenset[str]:
    """CPF_PREENCHIMENTO (separado por vírgula) da configuração; só entradas de 11 dígitos contam."""
    from app.core.config import get_settings

    return frozenset(x for x in (_NAO_DIGITO.sub("", p) for p in get_settings().cpf_preenchimento.split(",")) if len(x) == 11)


def classifica(cpf, origem_numerica: bool = False, preenchimento: frozenset[str] | None = None) -> Resultado:
    """CPF válido -> (11 dígitos, None). Vazio -> (None, None). Qualquer outro -> (None, 'cpf_invalido').

    Inválido = tamanho != 11, DV errado, dígitos todos iguais ou número da lista de preenchimento.
    O valor de entrada nunca vai para a mensagem nem para o log.
    """
    d = normaliza(cpf, origem_numerica=origem_numerica)
    if d is None:
        return Resultado(None, None)
    lista = lista_preenchimento() if preenchimento is None else preenchimento
    if not valido(d) or d in lista:
        return Resultado(None, MOTIVO_CPF_INVALIDO)
    return Resultado(d, None)
