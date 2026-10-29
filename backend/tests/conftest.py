"""Testes usam um Postgres de TESTE (TEST_DATABASE_URL). O banco é recriado do zero a cada sessão."""
import os

import pytest

TEST_URL = os.environ.get("TEST_DATABASE_URL")
if not TEST_URL:
    pytest.exit("Defina TEST_DATABASE_URL (Postgres de teste, nunca produção).", returncode=2)
os.environ["DATABASE_URL"] = TEST_URL
os.environ.setdefault("JWT_SECRET", "segredo-de-teste-somente-pytest")
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("SIGPMF_CHAVE_HASH_SEGREDO", "ZZTESTE-segredo-hmac-somente-pytest-0000")
os.environ.setdefault("AMOSTRA_LIMITE", "100")  # fixtures FX calibradas para amostra de 100

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SENHA = "Senha-de-teste-123"


def alembic_cfg() -> Config:
    cfg = Config(os.path.join(BACKEND, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(BACKEND, "alembic"))
    cfg.set_main_option("sqlalchemy.url", TEST_URL)
    return cfg


def zera_banco() -> None:
    eng = create_engine(TEST_URL)
    with eng.begin() as c:
        for s in ("bronze", "silver", "gold", "controle", "ref"):
            c.execute(text(f"DROP SCHEMA IF EXISTS {s} CASCADE"))
    eng.dispose()


@pytest.fixture(scope="session")
def banco():
    zera_banco()
    command.upgrade(alembic_cfg(), "head")
    yield TEST_URL


@pytest.fixture(scope="session")
def client(banco):
    from app.core.db import SessionLocal
    from app.core.security import hash_senha
    from app.main import app
    from app.models.controle import Usuario
    db = SessionLocal()
    for perfil in ("admin", "admin_lgpd", "operador", "leitura"):
        db.add(Usuario(email=f"{perfil}@aion.local", nome=f"Teste {perfil}", perfil=perfil, senha_hash=hash_senha(SENHA)))
    db.commit()
    db.close()
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="session")
def tokens(client):
    out = {}
    for perfil in ("admin", "admin_lgpd", "operador", "leitura"):
        r = client.post("/api/auth/login", json={"usuario": f"{perfil}@aion.local", "senha": SENHA})
        assert r.status_code == 200, r.text
        out[perfil] = {"Authorization": f"Bearer {r.json()['access_token']}"}
    return out


@pytest.fixture
def db(banco):
    from app.core.db import SessionLocal
    s = SessionLocal()
    yield s
    s.close()


@pytest.fixture(autouse=True)
def _amostra_sincrona(monkeypatch):
    """A rota da amostra responde 202 e roda em background (thread). Nos testes, o disparo é síncrono para que a
    resposta já traga o estado final; o teste do timeout (test_zzzzzz_amostra_background) usa a thread real."""
    from app.services import carga_conector as cc

    def sincrono(ex_id, conector, usuario, trava):
        cc.rodar_amostra(ex_id, conector, usuario, trava)
    monkeypatch.setattr(cc, "disparar_amostra", sincrono)


@pytest.fixture(autouse=True)
def _camadas_sem_assinatura(request):
    """IN1: cada teste começa sem a assinatura da última prata/ouro, para que uma carga completa reconstrua as camadas
    como antes (os testes compartilham o banco). Os testes de IN1 provam o "pular" dentro do próprio teste."""
    if "banco" not in request.fixturenames and "client" not in request.fixturenames and "db" not in request.fixturenames:
        yield
        return
    request.getfixturevalue("banco")  # garante que as migrações já rodaram (banco novo no CI)
    eng = create_engine(TEST_URL)
    with eng.begin() as c:
        c.execute(text("DELETE FROM controle.camadas_estado"))
    eng.dispose()
    yield
