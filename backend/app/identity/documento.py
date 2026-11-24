"""Documento (CPF/CNPJ) do Munícipe: normalização, validação e máscara (aceite M2/M7).

Toda comparação usa só dígitos (trim, sem . - / e espaços); vazio vira nulo.
CN1: o CNPJ alfanumérico (Receita, jul/2026: 12 posições [0-9A-Z] + 2 DV numéricos) mantém as letras, em maiúsculas;
o DV usa o valor ASCII − 48 de cada posição (dígitos 0–9, letras 17–42), o que não muda nada para o CNPJ numérico.
"""
import re
from typing import NamedTuple

from app.identity import cpf as cpfmod

_NAO_DIGITO = re.compile(r"\D")
_FORMATACAO = re.compile(r"[\s.\-/]")
_CNPJ_ALFA = re.compile(r"^[0-9A-Z]{12}[0-9]{2}$")


def cnpj_alfanumerico(doc) -> str | None:
    """CN1: o texto, sem formatação e em maiúsculas, se tiver o formato do CNPJ alfanumérico com ao menos uma letra."""
    if doc is None or isinstance(doc, int):
        return None
    c = _FORMATACAO.sub("", str(doc)).upper()
    return c if _CNPJ_ALFA.match(c) and not c.isdigit() else None


def normaliza(doc: str | None) -> str | None:
    if doc is None:
        return None
    alfa = cnpj_alfanumerico(doc)
    if alfa:
        return alfa
    d = _NAO_DIGITO.sub("", str(doc))
    return d or None


def cnpj_valido(doc: str | None) -> bool:
    d = normaliza(doc)
    if not d or not _CNPJ_ALFA.match(d) or d == d[0] * 14:
        return False

    def dv(parcial: str) -> str:
        pesos = list(range(len(parcial) - 7, 1, -1)) + list(range(9, 1, -1))
        r = sum((ord(c) - 48) * p for c, p in zip(parcial, pesos, strict=True)) % 11
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


MOTIVO_CNPJ_INVALIDO = "cnpj_invalido"
MOTIVO_DOCUMENTO_AMBIGUO = "documento_ambiguo"
_SO_FORMATACAO = re.compile(r"^[\d\s.\-/]+$")


class Classificacao(NamedTuple):
    tipo: str | None  # "cpf" | "cnpj" | None
    valor: str | None
    motivo: str | None  # None = válido


def classifica_cnpj(doc) -> Classificacao:
    """N5: 14 posições com DV válido (numérico ou alfanumérico, CN1); o resto vai para a quarentena `cnpj_invalido`."""
    d = normaliza(doc)
    if d is None:
        return Classificacao(None, None, None)
    if cnpj_valido(d):
        return Classificacao("cnpj", d, None)
    return Classificacao(None, None, MOTIVO_CNPJ_INVALIDO)


def classifica(doc, preenchimento: frozenset[str] | None = None) -> Classificacao:
    """N5: documento genérico -> CPF (11), CNPJ (14) ou inválido, pelo tamanho e pelo DV.

    Texto livre (letras) ou outro tamanho -> `documento_ambiguo`. Nada da entrada vai para a mensagem.
    """
    if doc is None or doc == "":
        return Classificacao(None, None, None)
    alfa = cnpj_alfanumerico(doc)
    if alfa:
        return classifica_cnpj(alfa)
    if not isinstance(doc, int) and not _SO_FORMATACAO.match(str(doc)):
        return Classificacao(None, None, MOTIVO_DOCUMENTO_AMBIGUO)
    d = normaliza(doc)
    if len(d) == 11:
        r = cpfmod.classifica(d, preenchimento=preenchimento)
        return Classificacao("cpf" if r.valor else None, r.valor, r.motivo)
    if len(d) == 14:
        return classifica_cnpj(d)
    return Classificacao(None, None, MOTIVO_DOCUMENTO_AMBIGUO)
