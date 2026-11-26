"""Regressão do access log do uvicorn sob o FiltroSegredos.

O uvicorn registra ``logger.info('%s - "%s %s HTTP/%s" %d', client_addr, method, full_path, http_version,
status_code)``. O AccessFormatter desempacota ``record.args``. Renderizar a mensagem e zerar args gerava
``TypeError: cannot unpack non-iterable NoneType object`` e a linha sumia. CPF e CNPJ são fictícios.
"""
import copy
import logging
import logging.config

import uvicorn.config

from app.core.seguranca_log import MASCARA_CNPJ, MASCARA_CPF, FiltroSegredos, instalar_filtro, mascarar
from app.identity.cpf import digitos_verificadores

CPF = "529982247" + digitos_verificadores("529982247")  # sintético, mesmo exemplo dos outros testes
CPF_FMT = f"{CPF[:3]}.{CPF[3:6]}.{CPF[6:9]}-{CPF[9:]}"
CNPJ = "11222333000181"  # sintético, DV válido, já usado nas fixtures
CNPJ_FMT = f"{CNPJ[:2]}.{CNPJ[2:5]}.{CNPJ[5:8]}/{CNPJ[8:12]}-{CNPJ[12:]}"
SEGREDO = "SEGREDO_FALSO"
MSG = '%s - "%s %s HTTP/%s" %d'
CLIENTE = "203.0.113.10:4242"


def _logging_do_uvicorn() -> None:
    cfg = copy.deepcopy(uvicorn.config.LOGGING_CONFIG)
    cfg["formatters"]["access"]["use_colors"] = False
    cfg["formatters"]["default"]["use_colors"] = False
    logging.config.dictConfig(cfg)
    instalar_filtro()
    instalar_filtro()  # idempotente, como o boot (import + lifespan)


def test_access_log_passa_pelo_formatter_sem_typeerror(capsys):
    _logging_do_uvicorn()
    lg = logging.getLogger("uvicorn.access")
    casos = [
        ("203.0.113.10:4242", "GET",
         f"/api/zz?cpf={CPF}&q=%s%20b&password={SEGREDO}", "1.1", 200, "200 OK"),
        ("203.0.113.10:4242", "GET",
         f"/nao-existe?cpf={CPF}&dsn=postgresql://zzteste:{SEGREDO}@localhost/x_test", "1.1", 404, "404 Not Found"),
        ("203.0.113.8:9", "POST", "/api/explodiu?cpf={cpf}".format(cpf=CPF), "1.1", 500, "500 Internal Server Error"),
    ]
    for client, method, path, version, status, _frase in casos:
        lg.info(MSG, client, method, path, version, status)
    out, err = capsys.readouterr()
    assert "Logging error" not in err and "TypeError" not in err
    assert SEGREDO not in out and SEGREDO not in err
    for client, method, path, version, _status, frase in casos:
        linha = f'INFO:     {client} - "{method} {mascarar(path)} HTTP/{version}" {frase}'
        assert linha in out
    assert MASCARA_CPF in out and CPF not in out


def test_access_log_mascara_cpf_e_cnpj_e_preserva_linha_sem_documento(capsys):
    _logging_do_uvicorn()
    lg = logging.getLogger("uvicorn.access")
    cpf_enc = CPF_FMT.replace(".", "%2E").replace("-", "%2D")
    cnpj_enc = CNPJ_FMT.replace(".", "%2e").replace("/", "%2f").replace("-", "%2d")
    sem_doc = "/api/health?q=a%20b&inscricao=1234567890123&telefone=16999887766&page=2"
    casos = [
        (f"/api/zz?cpf={CPF}", f"/api/zz?cpf={MASCARA_CPF}"),
        (f"/api/zz?cpf={CPF_FMT}", f"/api/zz?cpf={MASCARA_CPF}"),
        (f"/api/zz?cnpj={CNPJ}", f"/api/zz?cnpj={MASCARA_CNPJ}"),
        (f"/api/zz?cnpj={CNPJ_FMT}", f"/api/zz?cnpj={MASCARA_CNPJ}"),
        (f"/api/zz?documento={cpf_enc}", f"/api/zz?documento={MASCARA_CPF}"),
        (f"/api/zz?q={cnpj_enc}", f"/api/zz?q={MASCARA_CNPJ}"),
        (f"/cidadaos/{CPF}/genealogia", f"/cidadaos/{MASCARA_CPF}/genealogia"),
        (sem_doc, sem_doc),
    ]
    for path, _esperado in casos:
        lg.info(MSG, CLIENTE, "GET", path, "1.1", 200)
    out, err = capsys.readouterr()
    assert "Logging error" not in err and "TypeError" not in err
    assert CPF not in out and CPF_FMT not in out and cpf_enc not in out
    assert CNPJ not in out and CNPJ_FMT not in out and cnpj_enc not in out
    for _path, esperado in casos:
        assert f'INFO:     {CLIENTE} - "GET {esperado} HTTP/1.1" 200 OK' in out
    assert "1234567890123" in out and "16999887766" in out and "q=a%20b" in out


def test_outros_loggers_mascaram_cpf_na_mensagem(caplog):
    lg = logging.getLogger("sigpmf.teste_doc_log")
    caplog.handler.addFilter(FiltroSegredos())
    with caplog.at_level(logging.INFO, logger="sigpmf.teste_doc_log"):
        lg.info("busca documento=%s caminho=/cidadaos/%s/genealogia", CNPJ_FMT, CPF)
    assert CPF not in caplog.text and CNPJ_FMT not in caplog.text
    assert MASCARA_CPF in caplog.text and MASCARA_CNPJ in caplog.text
