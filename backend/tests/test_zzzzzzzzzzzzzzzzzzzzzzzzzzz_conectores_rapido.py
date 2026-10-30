"""GET /api/conectores e /api/conectores/{c}/completa rápidos (PR factory/sigpmf-conectores-rapido).

Prova que a listagem não abre conexão com nenhuma origem (SQL Server, SIL, SEI) e que, com as contagens pesadas
lentas (mock), a resposta sai em menos de 1 s servindo o retrato do cache. Só dados sintéticos ZZTESTE."""
import threading
import time

import pytest

from app.services import contagens_cache


class OrigemTocada(AssertionError):
    pass


def _proibe(*_a, **_k):
    raise OrigemTocada("a listagem tentou abrir conexão com a origem")


@pytest.fixture
def sem_origem(monkeypatch):
    """Qualquer tentativa de conectar em SQL Server/SIL/SEI explode o teste."""
    import pymssql

    from app.connectors.sei.cliente import ClienteSei
    from app.connectors.sil.cliente import ClienteSil
    from app.connectors.sil.conector import SilConnector
    from app.connectors.sqlserver import guardas
    from app.connectors.sqlserver.connector import SqlServerConnector
    from app.services import carga_conector as cc
    chamadas = []

    def marca(nome):
        def f(*a, **k):
            chamadas.append(nome)
            _proibe()
        return f
    monkeypatch.setattr(pymssql, "connect", marca("pymssql.connect"))
    monkeypatch.setattr(guardas, "conecta_guardado", marca("conecta_guardado"))
    monkeypatch.setattr(SqlServerConnector, "_conectar", marca("SqlServerConnector._conectar"))
    monkeypatch.setattr(SqlServerConnector, "testar_conexao", marca("SqlServerConnector.testar_conexao"))
    monkeypatch.setattr(SilConnector, "abrir", marca("SilConnector.abrir"))
    monkeypatch.setattr(ClienteSil, "_cliente_http", marca("ClienteSil._cliente_http"))
    monkeypatch.setattr(ClienteSei, "__init__", marca("ClienteSei.__init__"))
    monkeypatch.setattr(cc, "leitor_de", marca("carga_conector.leitor_de"))
    return chamadas


@pytest.fixture(autouse=True)
def cache_limpo():
    contagens_cache.limpa()
    yield
    contagens_cache.limpa()


@pytest.mark.parametrize("rota", ["/api/conectores", "/api/conectores/saude/completa",
                                  "/api/conectores/cemiterio/completa"])
def test_listagem_nao_abre_conexao_com_origem(client, tokens, sem_origem, rota):
    r = client.get(rota, headers=tokens["admin"])
    assert r.status_code == 200, r.text
    assert sem_origem == []
    assert "contagens_atualizando" in r.json()


def test_listagem_com_cache_quente_responde_em_menos_de_1s(client, tokens, sem_origem, monkeypatch):
    """Contagens pesadas simuladas com 3 s (produção levava 43–60 s): com retrato no cache a resposta é < 1 s."""
    from app.services import indicadores as ind
    real = ind.contagens_por_sistema
    lento = threading.Event()

    def contagens_lentas(db):
        if lento.is_set():
            time.sleep(3)
        return real(db)
    monkeypatch.setattr(ind, "contagens_por_sistema", contagens_lentas)
    monkeypatch.setenv("CONECTORES_CACHE_S", "60")
    monkeypatch.setenv("CONECTORES_CACHE_ESPERA_S", "0.5")
    h = tokens["admin"]
    assert client.get("/api/conectores", headers=h).status_code == 200  # aquece (rápido)
    lento.set()
    for _ in range(3):
        t = time.perf_counter()
        r = client.get("/api/conectores", headers=h)
        dt = time.perf_counter() - t
        assert r.status_code == 200 and dt < 1.0, dt
        assert r.json()["contagens_atualizando"] is False
    assert sem_origem == []


def test_retrato_velho_responde_rapido_e_marca_atualizando(client, tokens, monkeypatch):
    """TTL vencido + recálculo lento: devolve o retrato anterior em < 1 s, com contagens_atualizando=true."""
    from app.services import indicadores as ind
    real = ind.contagens_por_sistema
    atraso = {"s": 0.0}

    def contagens(db):
        time.sleep(atraso["s"])
        return real(db)
    monkeypatch.setattr(ind, "contagens_por_sistema", contagens)
    monkeypatch.setenv("CONECTORES_CACHE_S", "0.01")
    monkeypatch.setenv("CONECTORES_CACHE_ESPERA_S", "0.3")
    h = tokens["admin"]
    antes = client.get("/api/conectores", headers=h).json()
    atraso["s"] = 2.0
    time.sleep(0.05)
    t = time.perf_counter()
    r = client.get("/api/conectores", headers=h)
    assert time.perf_counter() - t < 1.0
    assert r.json()["contagens_atualizando"] is True
    assert r.json()["totais"] == antes["totais"]


def test_sem_retrato_e_calculo_lento_devolve_null_sem_travar(client, tokens, monkeypatch):
    from app.services import indicadores as ind
    real = ind.contagens_por_sistema
    monkeypatch.setattr(ind, "contagens_por_sistema", lambda db: (time.sleep(2), real(db))[1])
    monkeypatch.setenv("CONECTORES_CACHE_S", "60")
    monkeypatch.setenv("CONECTORES_CACHE_ESPERA_S", "0.2")
    t = time.perf_counter()
    r = client.get("/api/conectores", headers=tokens["admin"])
    assert r.status_code == 200 and time.perf_counter() - t < 1.0
    assert r.json()["contagens_atualizando"] is True
    j = r.json()
    assert j["totais"] == {"bronze": None, "prata": None, "ouro": None, "pendentes": None}  # nunca 0 com cache frio
    assert j["bases"] and all(b["contagens"] == {"bronze": None, "prata": None, "ouro": None, "pendentes": None}
                              for b in j["bases"])


def test_recalculo_com_erro_nao_vaza_mensagem(client, tokens, monkeypatch):
    """Falha no recálculo: a rota segue 200, sem texto da exceção na resposta."""
    from app.services import indicadores as ind

    def quebra(db):
        raise RuntimeError("ZZTESTE-segredo-host=10.0.0.1 senha=xyz")
    monkeypatch.setattr(ind, "contagens_por_sistema", quebra)
    r = client.get("/api/conectores", headers=tokens["admin"])
    assert r.status_code == 200
    assert "ZZTESTE-segredo" not in r.text and "10.0.0.1" not in r.text
    assert r.json()["contagens_atualizando"] is True


def test_cache_recalcula_quando_execucao_muda(db):
    """Chave do retrato muda quando uma execução começa/termina: a contagem nova aparece sem esperar o TTL."""
    from datetime import UTC, datetime

    from app.models.controle import Execucao
    n = {"v": 0}

    def conta(_db):
        n["v"] += 1
        return n["v"]
    import os
    os.environ["CONECTORES_CACHE_S"] = "600"
    try:
        v1, _ = contagens_cache.obter(db, "zzteste", conta)
        v2, _ = contagens_cache.obter(db, "zzteste", conta)
        assert v1 == v2 == 1
        db.add(Execucao(tipo="zzteste", status="sucesso", usuario="zzteste@aion.local",
                        iniciado_em=datetime.now(UTC), finalizado_em=datetime.now(UTC)))
        db.commit()
        v3, _ = contagens_cache.obter(db, "zzteste", conta)
        assert v3 == 2
    finally:
        os.environ.pop("CONECTORES_CACHE_S", None)


def test_cache_frio_completa_devolve_null_e_atualizando(client, tokens, monkeypatch):
    """/saude/completa sem retrato: camadas e quarentena null (nunca 0) + contagens_atualizando=true."""
    from app.services import carga_completa as cc
    real = cc.contagens_card
    monkeypatch.setattr(cc, "contagens_card", lambda db, s: (time.sleep(2), real(db, s))[1])
    monkeypatch.setenv("CONECTORES_CACHE_S", "60")
    monkeypatch.setenv("CONECTORES_CACHE_ESPERA_S", "0.2")
    j = client.get("/api/conectores/saude/completa", headers=tokens["admin"]).json()
    assert j["contagens_atualizando"] is True
    assert j["camadas"] == {"bronze": None, "prata": None, "pendencias": None, "gerados_prata": None}
    assert j["quarentena"] is None


def test_cache_quente_devolve_numeros(client, tokens, monkeypatch):
    """Retrato pronto: contagens numéricas (ZZTESTE sintético) e atualizando=false, nas duas rotas."""
    monkeypatch.setenv("CONECTORES_CACHE_S", "60")
    h = tokens["admin"]
    for _ in range(2):
        j = client.get("/api/conectores", headers=h).json()
    assert j["contagens_atualizando"] is False
    assert all(isinstance(v, int) for v in j["totais"].values())
    assert all(isinstance(v, int) for b in j["bases"] for v in b["contagens"].values())
    for _ in range(2):
        c = client.get("/api/conectores/saude/completa", headers=h).json()
    assert c["contagens_atualizando"] is False
    assert all(isinstance(v, int) for v in c["camadas"].values()) and isinstance(c["quarentena"], int)


def test_health_traz_migracao(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    from sqlalchemy import text

    from app.core.db import SessionLocal
    with SessionLocal() as s:
        try:
            esperado = s.execute(text("select version_num from alembic_version")).scalar()
        except Exception:  # noqa: BLE001 - banco de teste sem alembic_version
            esperado = None
    assert "migracao" in r.json() and r.json()["migracao"] == esperado


def test_health_migracao_falha_vira_null_sem_vazar(client, monkeypatch):
    from app.api import rotas

    # falha real na leitura: tabela inexistente simulada no SQL
    orig = rotas.text

    def text_falso(sql):
        return orig("select version_num from zzteste_tabela_inexistente") if "alembic_version" in sql else orig(sql)
    monkeypatch.setattr(rotas, "text", text_falso)
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json()["db"] == "ok"
    assert r.json()["migracao"] is None
    assert "zzteste_tabela_inexistente" not in r.text and "ZZTESTE" not in r.text
