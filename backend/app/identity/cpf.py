"""Validação, normalização e máscara de CPF."""
import re

_NAO_DIGITO = re.compile(r"\D")


def normaliza(cpf: str | None) -> str | None:
    if not cpf:
        return None
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
