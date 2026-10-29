"""PR-01 normalizadores (N1-N9, N3a). Só dados sintéticos; nenhum banco é tocado."""
import logging
import uuid

import pytest

from app.identity import cep, correlacao, documento, inscricao_imobiliaria, nis
from app.identity import cpf as cpfmod
from app.identity.cpf import digitos_verificadores

PREENCH = frozenset({"12345678909"})
CPF_ZERO = "01234567890"  # sintético, válido, com zero à esquerda (N1)


def _cpf(base9: str) -> str:
    return base9 + digitos_verificadores(base9)


def _cnpj(base12: str) -> str:
    def dv(p):
        pesos = list(range(len(p) - 7, 1, -1)) + list(range(9, 1, -1))
        r = sum(int(c) * w for c, w in zip(p, pesos, strict=True)) % 11
        return "0" if r < 2 else str(11 - r)
    d1 = dv(base12)
    return base12 + d1 + dv(base12 + d1)


def _nis(base10: str) -> str:
    return base10 + nis.digito_verificador(base10)


# N1
def test_n1_bigint_recebe_zero_a_esquerda():
    assert cpfmod.normaliza(1234567890) == CPF_ZERO
    assert cpfmod.classifica(1234567890, preenchimento=PREENCH) == (CPF_ZERO, None)
    assert cpfmod.normaliza(0) is None and cpfmod.normaliza(None) is None and cpfmod.normaliza("") is None


def test_n1_bigint_como_string_com_origem_numerica():
    assert cpfmod.normaliza("1234567890", origem_numerica=True) == CPF_ZERO
    assert cpfmod.normaliza(" 1234567890.0 ", origem_numerica=True) == CPF_ZERO
    assert cpfmod.normaliza("0", origem_numerica=True) is None
    assert cpfmod.normaliza("1234567890") == "1234567890"  # sem a flag não completa (texto)
    assert cpfmod.normaliza("123.456.789-0", origem_numerica=True) == "1234567890"  # formatado não é numérico


# N2
def test_n2_pontuacao_e_espacos():
    c = _cpf("987654321")
    assert cpfmod.classifica(f" {c[:3]}.{c[3:6]}.{c[6:9]}-{c[9:]} ", preenchimento=PREENCH) == (c, None)


# N3 / N3a
def _dv_errado(c: str) -> str:
    return c[:10] + str((int(c[10]) + 1) % 10)


@pytest.mark.parametrize("entrada", ["11111111111", "00000000000", "12345678909", _dv_errado(_cpf("987654321")), "123", "1234567890123"])
def test_n3_invalido_vai_para_quarentena(entrada):
    r = cpfmod.classifica(entrada, preenchimento=PREENCH)
    assert r == (None, "cpf_invalido")


def test_n3_vazio_nao_e_quarentena():
    assert cpfmod.classifica(None, preenchimento=PREENCH) == (None, None)


def test_n3a_lista_vem_da_configuracao(monkeypatch):
    from app.core.config import get_settings
    assert "12345678909" in cpfmod.lista_preenchimento()  # valor inicial
    outro = _cpf("135792468")
    monkeypatch.setenv("CPF_PREENCHIMENTO", f"12345678909, {outro[:3]}.{outro[3:6]}.{outro[6:9]}-{outro[9:]},lixo")
    get_settings.cache_clear()
    try:
        assert cpfmod.lista_preenchimento() == frozenset({"12345678909", outro})
        assert cpfmod.classifica(outro) == (None, "cpf_invalido")
    finally:
        get_settings.cache_clear()


def test_n3_invalido_nao_gera_uuid5(caplog):
    caplog.set_level(logging.DEBUG)
    for ruim in ("11111111111", "1234567890", "abc", "", None):
        with pytest.raises(ValueError) as e:
            correlacao.cidadao_id_por_cpf(ruim)
        assert str(e.value) == "cpf_invalido"
    assert "1234567890" not in caplog.text


# N4
def test_n4_mesmo_cpf_em_formatos_diferentes_mesmo_id():
    formatos = [1234567890, "1234567890.0", CPF_ZERO, "012.345.678-90", " 012 345 678 90 "]
    ids = set()
    for f in formatos:
        r = cpfmod.classifica(f, origem_numerica=True, preenchimento=PREENCH)
        ids.add(correlacao.cidadao_id_por_cpf(r.valor))
    assert len(ids) == 1
    assert correlacao.cidadao_id_por_cpf("012.345.678-90") == ids.pop()


# N5
def test_n5_cnpj():
    c = _cnpj("112223330001")
    assert documento.classifica_cnpj(c) == ("cnpj", c, None)
    assert documento.classifica_cnpj(f"{c[:2]}.{c[2:5]}.{c[5:8]}/{c[8:12]}-{c[12:]}") == ("cnpj", c, None)
    assert documento.classifica_cnpj(c[:13] + str((int(c[13]) + 1) % 10)) == (None, None, "cnpj_invalido")
    assert documento.classifica_cnpj("1" * 14) == (None, None, "cnpj_invalido")
    assert documento.classifica_cnpj(None) == (None, None, None)


def test_n5_documento_generico():
    c, j = _cpf("246813579"), _cnpj("998887770001")
    assert documento.classifica(c, preenchimento=PREENCH) == ("cpf", c, None)
    assert documento.classifica(j) == ("cnpj", j, None)
    assert documento.classifica(1234567890, preenchimento=PREENCH) == (None, None, "documento_ambiguo")  # int de 10 dígitos
    assert documento.classifica("12345678909", preenchimento=PREENCH) == (None, None, "cpf_invalido")
    assert documento.classifica("1" * 14) == (None, None, "cnpj_invalido")
    assert documento.classifica("12345") == (None, None, "documento_ambiguo")
    assert documento.classifica("ZZTESTE sem documento") == (None, None, "documento_ambiguo")
    assert documento.classifica("") == (None, None, None) and documento.classifica(None) == (None, None, None)


# N6
def test_n6_inscricao():
    assert inscricao_imobiliaria.normaliza("12.3.45.67.890.12.34") == ("12345678901234", None)
    assert inscricao_imobiliaria.normaliza("123456789012") == ("00123456789012", "inscricao_completada")
    assert inscricao_imobiliaria.normaliza(1234567890123) == ("01234567890123", "inscricao_completada")
    for ruim in ("12345678901", "123456789012345", "1" * 20, "ZZTESTE"):
        assert inscricao_imobiliaria.normaliza(ruim) == (None, "inscricao_invalida")
    assert inscricao_imobiliaria.normaliza(None) == (None, None) and inscricao_imobiliaria.normaliza("") == (None, None)
    assert inscricao_imobiliaria.normaliza(True) == (None, None)


# N7
def test_n7_cep():
    assert cep.normaliza("14400-000") == ("14400000", None)
    assert cep.normaliza(1440000) == ("01440000", None)
    assert cep.normaliza("12345") == ("00012345", None)
    assert cep.normaliza("1") == (None, "cep_lixo") and cep.normaliza("00000000") == (None, "cep_lixo")
    assert cep.normaliza("ZZ") == (None, "cep_lixo")
    assert cep.normaliza("123456789") == (None, "cep_invalido")
    assert cep.normaliza(None) == (None, None) and cep.normaliza("") == (None, None) and cep.normaliza(False) == (None, None)


def test_n7_nis():
    n = _nis("1203456789")
    assert nis.normaliza(n) == (n, None)
    assert nis.normaliza(f"{n[:3]}.{n[3:8]}.{n[8:10]}-{n[10]}") == (n, None)
    assert nis.normaliza(int(_nis("0123456789"))) == (_nis("0123456789"), None)
    assert nis.normaliza(n[:10] + str((int(n[10]) + 1) % 10)) == (None, "nis_invalido")
    assert nis.normaliza("2" * 11) == (None, "nis_invalido") and nis.normaliza("123") == (None, "nis_invalido")
    assert nis.normaliza(None) == (None, None) and nis.normaliza(True) == (None, None)
    # ramo r>=10 -> DV 0
    assert any(nis.digito_verificador(f"{i:010d}") == "0" for i in range(1, 200))


# N8: nenhuma entrada em mensagem de erro ou log
def test_n8_sem_valor_em_mensagem(caplog):
    caplog.set_level(logging.DEBUG)
    ruins = ["11111111111", "ZZTESTE 999", "123456789012345"]
    saidas = [cpfmod.classifica(ruins[0], preenchimento=PREENCH), documento.classifica(ruins[1]),
              inscricao_imobiliaria.normaliza(ruins[2]), cep.normaliza(ruins[2]), nis.normaliza(ruins[0])]
    for s in saidas:
        assert all(r not in str(s) for r in ruins)
    assert caplog.text == ""


# N9 regressão: ids já existentes não mudam
def test_n9_ids_existentes_nao_mudam():
    ns = uuid.UUID("6f1c2a52-3b7e-4c1e-9a0d-5f0a1c2b3d4e")
    assert correlacao.NAMESPACE == ns
    assert correlacao.cidadao_id_por_cpf(CPF_ZERO) == "7aa5d849-47f0-5690-9757-ea69f89c0229"
    for base in ("987654321", "246813579", "135792468"):
        c = _cpf(base)
        assert correlacao.cidadao_id_por_cpf(c) == str(uuid.uuid5(ns, f"cpf:{c}"))
    assert correlacao.cidadao_id_por_origem("sil", "t", "1") == str(uuid.uuid5(ns, "origem:sil:t:1"))


def test_n8_ramos_de_mascara_e_valido_legados():
    c, j = _cpf("246813579"), _cnpj("998887770001")
    assert cpfmod.mascara(None) is None and cpfmod.mascara("123") == "***.***.***-**"
    assert cpfmod.mascara_dict({"cpf_x": c, "nome": "ZZTESTE", "cpf_n": 1}) == {"cpf_x": cpfmod.mascara(c), "nome": "ZZTESTE", "cpf_n": 1}
    assert documento.normaliza(None) is None and documento.normaliza("--") is None
    assert documento.valido(c, "F") and documento.valido(j, "J") and not documento.valido(c, "X")
    assert documento.mascara(None) is None and documento.mascara(j).startswith("**.") and documento.mascara(c) == cpfmod.mascara(c)
    assert not documento.cnpj_valido(None)
