"""SIL_AMOSTRA_MAX_LOTES e demais números da SIL: valor inválido nunca quebra a amostra (padrão + WARNING sem valor)."""
import logging

import pytest

from app.connectors.sil import carga
from app.connectors.sil.config import ConfigSil
from app.services import amostra_sil as sil

SEGREDO = "ZZTESTE-segredo-nao-logar"
INVALIDOS = ["nan", "NaN", "inf", "-inf", "-5", "0", "abc", "1e400", "1e30", "101", "2.5", SEGREDO]


@pytest.mark.parametrize("valor", INVALIDOS)
def test_amostra_max_lotes_invalido_usa_padrao_e_avisa(valor, monkeypatch, caplog):
    monkeypatch.setenv("SIL_AMOSTRA_MAX_LOTES", valor)
    with caplog.at_level(logging.WARNING, logger="sigpmf.sil"):
        assert sil.lotes_por_execucao() == sil.LOTES_AMOSTRA_PADRAO
    avisos = [r for r in caplog.records if r.levelno == logging.WARNING and "SIL_AMOSTRA_MAX_LOTES" in r.getMessage()]
    assert avisos and all(valor not in r.getMessage() for r in avisos if valor not in ("0",))
    assert SEGREDO not in caplog.text


@pytest.mark.parametrize("valor,esperado", [("1", 1), ("20", 20), (" 100 ", 100), ("7.0", 7), ("", 20)])
def test_amostra_max_lotes_valido(valor, esperado, monkeypatch, caplog):
    monkeypatch.setenv("SIL_AMOSTRA_MAX_LOTES", valor)
    with caplog.at_level(logging.WARNING, logger="sigpmf.sil"):
        assert sil.lotes_por_execucao() == esperado
    assert not caplog.records


@pytest.mark.parametrize("nome", ["SIL_TIMEOUT_S", "SIL_TIMEOUT", "SIL_TENTATIVAS", "SIL_REQ_POR_S",
                                  "SIL_INTERVALO_MS", "SIL_MAX_LOTES_POR_EXECUCAO"])
@pytest.mark.parametrize("valor", ["nan", "inf", "-inf", SEGREDO])
def test_demais_numeros_sil_nan_inf_texto(nome, valor, monkeypatch, caplog):
    for n in ("SIL_TIMEOUT_S", "SIL_TIMEOUT", "SIL_TENTATIVAS", "SIL_REQ_POR_S", "SIL_INTERVALO_MS",
              "SIL_MAX_LOTES_POR_EXECUCAO"):
        monkeypatch.delenv(n, raising=False)
    padrao = ConfigSil.do_ambiente()
    monkeypatch.setenv(nome, valor)
    with caplog.at_level(logging.WARNING, logger="sigpmf.sil"):
        c = ConfigSil.do_ambiente()
    assert (c.timeout_s, c.tentativas, c.req_por_s, c.max_lotes_por_execucao) == \
        (padrao.timeout_s, padrao.tentativas, padrao.req_por_s, padrao.max_lotes_por_execucao)
    assert nome in caplog.text and SEGREDO not in caplog.text


@pytest.mark.parametrize("valor", ["nan", "inf", "1.5", SEGREDO])
def test_consultarlote_max_lotes_invalido(valor, monkeypatch, caplog):
    monkeypatch.setenv("SIL_CONSULTARLOTE_MAX_LOTES", valor)
    with caplog.at_level(logging.WARNING, logger="sigpmf.sil"):
        assert carga._lotes_configurados() == 1
    assert "SIL_CONSULTARLOTE_MAX_LOTES" in caplog.text and SEGREDO not in caplog.text

