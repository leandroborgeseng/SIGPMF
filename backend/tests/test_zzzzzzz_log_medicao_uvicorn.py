"""Ciclo 1 do QA (#24): sob o LOGGING_CONFIG do uvicorn, o INFO de app.silver.medicao precisa sair em stdout (antes era
descartado: a aplicação não configurava logging e o uvicorn só configura os loggers dele)."""
import logging
import logging.config

import uvicorn.config


def _reset():
    med = logging.getLogger("app.silver.medicao")
    med.handlers.clear()
    med.setLevel(logging.NOTSET)
    med.propagate = True
    return med


def test_sem_configuracao_o_info_some(capsys):
    med = _reset()
    raiz = logging.getLogger()
    antigos, nivel = raiz.handlers[:], raiz.level
    raiz.handlers.clear()
    raiz.setLevel(logging.WARNING)
    try:
        logging.config.dictConfig(uvicorn.config.LOGGING_CONFIG)
        med.info("prata etapa=zz_antes segundos=0.000")
        assert "zz_antes" not in capsys.readouterr().out  # reprodução do bug
    finally:
        raiz.handlers[:] = antigos
        raiz.setLevel(nivel)


def test_info_da_medicao_sai_uma_vez_em_stdout_sob_uvicorn(capsys):
    from app.main import configurar_logs
    _reset()
    logging.config.dictConfig(uvicorn.config.LOGGING_CONFIG)  # o uvicorn CLI configura antes de importar o app
    configurar_logs()
    configurar_logs()  # idempotente: não empilha handler
    logging.config.dictConfig(uvicorn.config.LOGGING_CONFIG)  # e reconfigurar depois não desliga (disable_existing=False)
    logging.getLogger("app.silver.medicao").info("prata etapa=zz_teste segundos=%.3f", 1.5)
    out = capsys.readouterr().out
    assert out.count("prata etapa=zz_teste segundos=1.500") == 1
    assert logging.getLogger("app.silver.medicao").propagate is False
