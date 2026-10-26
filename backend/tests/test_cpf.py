from app.identity.cpf import digitos_verificadores, mascara, normaliza, valido


def _gera(base: str) -> str:
    return base + digitos_verificadores(base)


def test_valido_com_e_sem_mascara():
    c = _gera("123456789")
    f = f"{c[:3]}.{c[3:6]}.{c[6:9]}-{c[9:]}"
    assert valido(c) and valido(f) and valido(f" {f} ")
    assert normaliza(f) == normaliza(c)


def test_invalidos():
    c = _gera("987654321")
    assert not valido(c[:10] + str((int(c[10]) + 1) % 10))
    for d in "0123456789":
        assert not valido(d * 11)
    assert not valido(c[:10]) and not valido(c + "1") and not valido("abc") and not valido(None)


def test_mascara_formato():
    c = _gera("123456789")
    assert mascara(c) == "***.456.789-**"
