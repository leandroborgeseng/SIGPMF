"""Regressão do access log do uvicorn sob o FiltroSegredos.

O uvicorn registra ``logger.info('%s - "%s %s HTTP/%s" %d', client_addr, method, full_path, http_version,
status_code)``. O AccessFormatter desempacota ``record.args``. Renderizar a mensagem e zerar args gerava
``TypeError: cannot unpack non-iterable NoneType object`` e a linha sumia. CPF fictício; senha/DSN continuam
mascarados. Se ``mascarar`` passar a mascarar CPF, a saída esperada acompanha.
"""
import copy
import logging
import logging.config

import uvicorn.config

from app.core.seguranca_log import instalar_filtro, mascarar
from app.identity.cpf import digitos_verificadores

CPF = "529982247" + digitos_verificadores("529982247")  # sintético, mesmo exemplo dos outros testes
SEGREDO = "SEGREDO_FALSO"
MSG = '%s - "%s %s HTTP/%s" %d'


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
    if mascarar(CPF) != CPF:
        assert mascarar(CPF) in out and CPF not in out
    else:
        assert f"cpf={CPF}" in out
