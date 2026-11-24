"""DSN/senha nunca aparecem em log, resposta da API nem saída de CLI (TL: mascarar DSN no erro do psycopg)."""
import logging
import os
import subprocess
import sys

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

from app.core.seguranca_log import FiltroSegredos, instalar_filtro, mascarar, mensagem_erro_banco

SEGREDO = "SEGREDO_FALSO"
DSN = f"postgresql://zzteste:{SEGREDO}@localhost/x_test"
BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.mark.parametrize("entrada,esperado", [
    (DSN, "postgresql://zzteste:***@localhost/x_test"),
    (f"postgresql+psycopg://zzteste:{SEGREDO}@db:5432/x_test?sslmode=require",
     "postgresql+psycopg://zzteste:***@db:5432/x_test?sslmode=require"),
    (f"host=localhost user=zzteste password={SEGREDO} dbname=x_test", "host=localhost user=zzteste password=*** dbname=x_test"),
    (f"password='{SEGREDO} com espaco' host=x", "password=*** host=x"),
    (f"PGPASSWORD={SEGREDO};x", "PGPASSWORD=***;x"),
    ("postgresql://zzteste@localhost/x_test", "postgresql://zzteste@localhost/x_test"),
])
def test_mascarar(entrada, esperado):
    assert mascarar(entrada) == esperado
    assert SEGREDO not in mascarar(entrada)


def _erro_conexao() -> OperationalError:
    eng = create_engine(DSN.replace("localhost", "127.0.0.1:1"), pool_pre_ping=False)
    try:
        with eng.connect() as c:
            c.execute(text("select 1"))
    except OperationalError as exc:
        return OperationalError(f"falha ao conectar em {DSN}", {}, exc.orig)
    finally:
        eng.dispose()
    raise AssertionError("conexão deveria falhar")


def test_log_mascara_mensagem_args_e_traceback(caplog):
    lg = logging.getLogger("sigpmf.teste_dsn")
    caplog.handler.addFilter(FiltroSegredos())
    exc = _erro_conexao()
    with caplog.at_level(logging.INFO, logger="sigpmf.teste_dsn"):
        lg.info("conectando em %s", DSN)
        lg.warning(f"kv: password={SEGREDO} host=x")
        try:
            raise RuntimeError(f"wrap {DSN}") from exc
        except RuntimeError:
            lg.exception("falhou")
    assert "zzteste:***@localhost" in caplog.text
    assert SEGREDO not in caplog.text
    for r in caplog.records:
        assert SEGREDO not in r.getMessage()
        assert SEGREDO not in (r.exc_text or "")


def test_instalar_filtro_no_raiz(capsys):
    h = logging.StreamHandler(sys.stderr)
    logging.getLogger().addHandler(h)
    try:
        instalar_filtro()
        instalar_filtro()  # idempotente
        assert sum(isinstance(f, FiltroSegredos) for f in h.filters) == 1
        logging.getLogger().error("dsn %s", DSN)
    finally:
        logging.getLogger().removeHandler(h)
    err = capsys.readouterr().err
    assert "zzteste:***@" in err and SEGREDO not in err


def test_api_devolve_so_a_classe(banco, monkeypatch, caplog):
    from fastapi.testclient import TestClient

    from app.main import create_app
    app = create_app()
    exc = _erro_conexao()

    @app.get("/api/_teste_dsn")
    def _explode():
        raise exc

    with TestClient(app, raise_server_exceptions=False) as cli, caplog.at_level(logging.INFO):
        r = cli.get("/api/_teste_dsn")
    assert r.status_code == 503
    assert r.json() == {"detail": mensagem_erro_banco(exc)}
    assert "OperationalError" in r.text
    assert SEGREDO not in r.text and "zzteste" not in r.text
    assert SEGREDO not in caplog.text


def _roda(args, env_extra):
    env = {**os.environ, **env_extra}
    return subprocess.run([sys.executable, *args], cwd=BACKEND, env=env, capture_output=True, text=True, timeout=120)


def test_cli_exemplo_nao_imprime_dsn():
    p = _roda(["-m", "app.paineis.exemplo"],
              {"DATABASE_URL": f"postgresql://zzteste:{SEGREDO}@127.0.0.1:1/x_test"})
    saida = p.stdout + p.stderr
    assert p.returncode != 0
    assert "Falha de acesso ao banco (OperationalError)." in saida
    assert SEGREDO not in saida and "Traceback" not in saida


def test_cli_alembic_nao_imprime_dsn():
    p = _roda(["-m", "alembic", "current"],
              {"DATABASE_URL": f"postgresql://zzteste:{SEGREDO}@127.0.0.1:1/x_test"})
    saida = p.stdout + p.stderr
    assert p.returncode != 0
    assert "Falha de acesso ao banco (OperationalError)." in saida
    assert SEGREDO not in saida
