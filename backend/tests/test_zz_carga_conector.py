# Roda por último (zz): grava bronze real-like de SAUDE/CEMITERIO que mudaria as contagens dos testes de demo.
"""Carga real por conector (PB12/PB13 + critérios do Tester). Só dados sintéticos (OrigemMock)."""
import pytest
from sqlalchemy import func, select

from app.connectors.sqlserver.connector import ConfigSqlServer
from app.ingestion.origem_mock import OrigemMock
from app.models.camadas import RegistroBronze
from app.models.controle import Auditoria, Execucao, Log
from app.services import carga_conector as cc

HOST, USER, PWD = "srv-sintetico.invalid", "usuario_sintetico", "SenhaSintetica#123"


class Leitor:
    """Origem sintética com a mesma interface do SqlServerConnector."""
    def __init__(self, sistema, falha_em=None):
        self.o, self.sistema, self.falha_em = OrigemMock(conjunto="saude+cemiterio"), sistema, falha_em

    def ler_amostra(self, tabela, politica=None):
        if tabela == self.falha_em:
            raise TimeoutError(f"timeout conectando em {HOST} como {USER}")
        if (self.sistema, tabela) not in self.o.tabelas():
            return []
        return self.o.ler(self.sistema, tabela, 100, politica.colunas_select(tabela))


@pytest.fixture
def leitor(monkeypatch):
    estado = {"falha_em": None}

    def fake(db, usuario, conector, leitor=None, _orig=cc.executar_amostra):
        sis = cc.CONECTORES.get(conector, ("X",))[0]
        return _orig(db, usuario, conector, leitor=Leitor(sis, estado["falha_em"]))
    monkeypatch.setattr(cc, "executar_amostra", fake)
    monkeypatch.setenv("SQLSERVER_HOST", HOST)
    monkeypatch.setenv("SQLSERVER_USER", USER)
    monkeypatch.setenv("SQLSERVER_PASSWORD", PWD)
    monkeypatch.setenv("SQLSERVER_DATABASE", "BancoGeralSintetico")
    monkeypatch.setenv("SQLSERVER_DATABASE_SAUDE", "Saude")
    from app.core.config import get_settings
    get_settings.cache_clear()
    yield estado
    get_settings.cache_clear()


def _bronze(db, sistema):
    return db.scalar(select(func.count()).select_from(RegistroBronze).where(RegistroBronze.sistema_origem == sistema))


def test_banco_por_conector_e_fallback(leitor):
    from app.core.config import get_settings
    c, fb = ConfigSqlServer.para_conector(get_settings(), "saude")
    assert c.database == "Saude" and fb is False
    c, fb = ConfigSqlServer.para_conector(get_settings(), "cemiterio")
    assert c.database == "BancoGeralSintetico" and fb is True


def test_repr_da_config_mascara_credenciais(leitor):
    from app.core.config import get_settings
    c, _ = ConfigSqlServer.para_conector(get_settings(), "saude")
    for txt in (repr(c), str(c), f"{c}"):
        assert HOST not in txt and USER not in txt and PWD not in txt


def test_permissoes_e_auditoria(client, tokens, leitor, db):
    assert client.post("/api/conectores/saude/amostra").status_code == 401
    for perfil in ("leitura", "operador"):
        assert client.post("/api/conectores/saude/amostra", headers=tokens[perfil]).status_code == 403
    antes = db.scalar(select(func.count()).select_from(Auditoria).where(Auditoria.alvo == "conector:cemiterio",
                                                                        Auditoria.acao == "amostra_conector_solicitada"))
    for _ in range(2):
        r = client.post("/api/conectores/cemiterio/amostra", headers=tokens["admin"])
        assert r.status_code == 200 and r.json()["status"] != "falha", r.text
    depois = db.scalar(select(func.count()).select_from(Auditoria).where(Auditoria.alvo == "conector:cemiterio",
                                                                         Auditoria.acao == "amostra_conector_solicitada"))
    assert depois - antes == 2
    assert client.post("/api/conectores/naoexiste/amostra", headers=tokens["admin"]).status_code == 404


def test_amostra_grava_bronze_e_repetir_nao_duplica(client, tokens, leitor, db):
    r = client.post("/api/conectores/saude/amostra", headers=tokens["admin"])
    assert r.status_code == 200, r.text
    ex = db.get(Execucao, r.json()["id"])
    assert ex.status in ("sucesso", "sem_novidades") and ex.finalizado_em and ex.lidos > 0, ex.mensagem
    assert ex.lidos == ex.novos + ex.atualizados + ex.ignorados
    n1 = _bronze(db, "SAUDE")
    r2 = client.post("/api/conectores/saude/amostra", headers=tokens["admin"])
    db.expire_all()
    ex2 = db.get(Execucao, r2.json()["id"])
    assert _bronze(db, "SAUDE") == n1 and ex2.novos == 0 and ex2.atualizados == 0
    logs = " ".join(x.mensagem for x in db.scalars(select(Log).where(Log.execucao_id == ex.id)))
    assert "Saude" in logs and HOST not in logs and USER not in logs and PWD not in logs


def test_falha_na_origem_nao_deixa_carga_parcial(client, tokens, leitor, db):
    from app.services.carga_conector import tabelas_do_conector
    tabs = tabelas_do_conector("CEMITERIO")
    leitor["falha_em"] = tabs[-1]  # as primeiras tabelas leem bem, a última dá timeout
    antes = _bronze(db, "CEMITERIO")
    r = client.post("/api/conectores/cemiterio/amostra", headers=tokens["admin"])
    assert r.status_code == 200
    db.expire_all()
    ex = db.get(Execucao, r.json()["id"])
    assert ex.status == "falha" and ex.finalizado_em is not None and "timeout" in ex.mensagem
    assert _bronze(db, "CEMITERIO") == antes
    for t in (r.text, ex.mensagem):
        assert HOST not in t and USER not in t and PWD not in t


def test_amostra_concorrente_409(client, tokens, leitor, db):
    db.add(Execucao(tipo=cc.TIPO_AMOSTRA, sistema="SAUDE", status="em_andamento", usuario="outro@aion.local"))
    db.commit()
    try:
        r = client.post("/api/conectores/saude/amostra", headers=tokens["admin"])
        assert r.status_code == 409
    finally:
        for e in db.scalars(select(Execucao).where(Execucao.usuario == "outro@aion.local")):
            e.status = "falha"  # encerra a execução sintética (sem apagar nada)
        db.commit()


def test_amostra_concorrente_409_por_lock(client, tokens, leitor, db):
    import zlib

    from sqlalchemy import text
    k = zlib.crc32(b"amostra:SAUDE")
    from app.core.db import SessionLocal
    outra = SessionLocal()
    assert outra.scalar(text("select pg_try_advisory_lock(:k)"), {"k": k})
    try:
        assert client.post("/api/conectores/saude/amostra", headers=tokens["admin"]).status_code == 409
    finally:
        outra.execute(text("select pg_advisory_unlock(:k)"), {"k": k})
        outra.close()


def test_iss_sem_mapeamento_409(client, tokens, leitor):
    assert client.post("/api/conectores/iss/amostra", headers=tokens["admin"]).status_code == 409


def test_carga_completa_travada_sem_amostra(client, tokens, leitor, db):
    assert client.post("/api/conectores/iss/completa", headers=tokens["operador"]).status_code == 403
    r = client.post("/api/conectores/iss/completa", headers=tokens["admin"])
    assert r.status_code == 409
    assert client.post("/api/conectores/saude/amostra", headers=tokens["admin"]).status_code == 200
    assert client.post("/api/conectores/saude/completa", headers=tokens["admin"]).status_code == 501


def test_demo_bloqueada_fora_de_dev(client, tokens, monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    from app.core.config import get_settings
    get_settings.cache_clear()
    try:
        assert client.post("/api/conectores/demo/executar", headers=tokens["admin"]).status_code == 403
    finally:
        monkeypatch.setenv("APP_ENV", "test")
        get_settings.cache_clear()


def test_listagem_mostra_banco_sem_credencial(client, tokens, leitor):
    client.post("/api/conectores/saude/amostra", headers=tokens["admin"])
    r = client.get("/api/conectores", headers=tokens["leitura"])
    assert r.status_code == 200
    saude = next(c for c in r.json()["conectores"] if c.get("conector_id") == "saude")
    assert saude["banco_origem"] == "Saude" and saude["banco_origem_fallback"] is False
    assert HOST not in r.text and USER not in r.text and PWD not in r.text
