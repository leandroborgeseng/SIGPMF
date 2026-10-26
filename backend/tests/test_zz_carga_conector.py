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
        if self.sistema == "ISS":
            politica.colunas_select(tabela)  # exige a política ISS (Munícipe)
            if self.falha_em:
                raise TimeoutError(f"timeout conectando em {HOST} como {USER}")
            return linhas_iss()
        if tabela == self.falha_em:
            raise TimeoutError(f"timeout conectando em {HOST} como {USER}")
        if (self.sistema, tabela) not in self.o.tabelas():
            return []
        return self.o.ler(self.sistema, tabela, 100, politica.colunas_select(tabela))


def _cpf(base9: int) -> str:
    from app.identity.cpf import digitos_verificadores
    b = f"{base9:09d}"
    return b + digitos_verificadores(b)


def linhas_iss():
    """Munícipes sintéticos: 2 F válidos (um duplicado), 1 J sem DV válido, 1 R."""
    a, b = _cpf(111222333), _cpf(444555666)
    base = {"Nome_Mae": "MAE SINTETICA", "Data_Nascimento": "1980-01-01", "Pis_Pasep": None}
    return [{**base, "Tipo_Pessoa": "F", "CPF": a, "Documento_SoNumeros": a, "Nome": "FULANO A",
             "Data_Alteracao": "2026-01-01T10:00:00"},
            {**base, "Tipo_Pessoa": "F", "CPF": a, "Documento_SoNumeros": a, "Nome": "FULANO A",
             "Data_Alteracao": "2026-02-01T10:00:00"},
            {**base, "Tipo_Pessoa": "F", "CPF": b, "Documento_SoNumeros": b, "Nome": "FULANA B",
             "Data_Alteracao": "2026-01-01T10:00:00"},
            {**base, "Tipo_Pessoa": "R", "CPF": "00000000000", "Documento_SoNumeros": "00000000000",
             "Nome": "TIPO R", "Data_Alteracao": "2026-01-01T10:00:00"}]


@pytest.fixture(scope="module", autouse=True)
def _reaplica_0006(banco):
    """Outros testes (#7) re-semeiam o catálogo (ISS volta a 'mapeado'); reaplica a 0006 como em produção."""
    from alembic import command

    from tests.conftest import alembic_cfg
    command.downgrade(alembic_cfg(), "0005")
    command.upgrade(alembic_cfg(), "head")


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


def test_carga_completa_travada_sem_amostra(client, tokens, leitor, db):
    assert client.post("/api/conectores/iss/completa", headers=tokens["operador"]).status_code == 403
    with pytest.MonkeyPatch.context() as mp:  # sem amostra reconciliada -> 409
        mp.setattr(cc, "amostra_reconciliada", lambda db, sistema: None)
        assert client.post("/api/conectores/saude/completa", headers=tokens["admin"]).status_code == 409
    assert client.post("/api/conectores/saude/amostra", headers=tokens["admin"]).status_code == 200
    assert client.post("/api/conectores/saude/completa", headers=tokens["admin"]).status_code == 501


@pytest.mark.parametrize("ambiente", ["development", "", None, "prod", "production", "DEV", " test"])
def test_demo_bloqueada_fora_de_dev_test(client, tokens, monkeypatch, db, ambiente):
    from app.core.config import get_settings
    if ambiente is None:
        monkeypatch.delenv("APP_ENV", raising=False)
    else:
        monkeypatch.setenv("APP_ENV", ambiente)
    get_settings.cache_clear()
    antes = db.scalar(select(func.count()).select_from(Auditoria).where(Auditoria.acao == "carga_demo_negada"))
    try:
        assert client.post("/api/conectores/demo/executar", headers=tokens["admin"]).status_code == 404
        db.expire_all()
        depois = db.scalar(select(func.count()).select_from(Auditoria).where(Auditoria.acao == "carga_demo_negada"))
        assert depois == antes + 1
    finally:
        monkeypatch.setenv("APP_ENV", "test")
        get_settings.cache_clear()


@pytest.mark.parametrize("ambiente", ["development", "prod"])
def test_cli_demo_tambem_bloqueado(db, monkeypatch, ambiente):
    from app.core.config import get_settings
    from app.services.pipeline import CargaSinteticaBloqueada, executar_demo
    monkeypatch.setenv("APP_ENV", ambiente)
    get_settings.cache_clear()
    try:
        with pytest.raises(CargaSinteticaBloqueada):
            executar_demo(db, "cli")
    finally:
        monkeypatch.setenv("APP_ENV", "test")
        get_settings.cache_clear()


@pytest.mark.parametrize("ambiente", ["dev", "test"])
def test_demo_permitida_em_dev_test(monkeypatch, ambiente):
    from app.core.config import get_settings
    from app.services.pipeline import carga_sintetica_permitida
    monkeypatch.setenv("APP_ENV", ambiente)
    get_settings.cache_clear()
    try:
        assert carga_sintetica_permitida()
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


def test_padrao_do_app_env_e_prod(monkeypatch):
    from app.core.config import Settings
    monkeypatch.delenv("APP_ENV", raising=False)
    assert Settings(_env_file=None).app_env == "prod"


# ---------- ISS (após o merge do #6): mesma rota, política do Munícipe ----------
def test_iss_amostra_usa_politica_do_municipe(client, tokens, leitor, db):
    from app.models.camadas import Municipe
    assert client.post("/api/conectores/iss/amostra", headers=tokens["operador"]).status_code == 403
    r = client.post("/api/conectores/iss/amostra", headers=tokens["admin"])
    assert r.status_code == 200, r.text
    db.expire_all()
    ex = db.get(Execucao, r.json()["id"])
    assert ex.tipo == cc.TIPO_AMOSTRA_ISS and ex.status in ("sucesso", "sem_novidades"), ex.mensagem
    assert ex.lidos == 4 and ex.duplicados == 1 and ex.pendentes == 1
    n = db.scalar(select(func.count()).select_from(Municipe))
    r2 = client.post("/api/conectores/iss/amostra", headers=tokens["admin"])
    db.expire_all()
    assert db.scalar(select(func.count()).select_from(Municipe)) == n and r2.json()["novos"] == 0
    assert client.post("/api/conectores/iss/completa", headers=tokens["admin"]).status_code == 501
    logs = " ".join(x.mensagem for x in db.scalars(select(Log).where(Log.execucao_id == ex.id)))
    assert "catálogo" in logs and HOST not in logs and USER not in logs and PWD not in logs


def test_amostra_reconciliada_exige_contagens(db):
    assert cc.amostra_reconciliada(db, "NENHUM") is None
    db.add(Execucao(tipo=cc.TIPO_AMOSTRA, sistema="NENHUM", status="sucesso", usuario="t@aion.local",
                    lidos=5, novos=1, atualizados=0, ignorados=0))
    db.commit()
    assert cc.amostra_reconciliada(db, "NENHUM") is None  # 5 != 1: não reconcilia


def test_iss_falha_sem_vazar_credencial(client, tokens, leitor, db):
    leitor["falha_em"] = "Municipes"
    r = client.post("/api/conectores/iss/amostra", headers=tokens["admin"])
    db.expire_all()
    ex = db.get(Execucao, r.json()["id"])
    assert ex.status == "falha" and ex.finalizado_em is not None
    for t in (r.text, ex.mensagem):
        assert HOST not in t and USER not in t and PWD not in t


@pytest.mark.parametrize("rota", ["/api/conectores/iss/municipes/executar", "/api/conectores/iss/amostra",
                                  "/api/conectores/iss/completa"])
def test_rotas_iss_admin_403_auditado(client, tokens, db, rota):
    assert client.post(rota).status_code == 401
    for perfil in ("leitura", "operador"):
        antes = db.scalar(select(func.count()).select_from(Auditoria).where(Auditoria.acao == "acesso_negado",
                                                                            Auditoria.alvo == rota))
        assert client.post(rota, headers=tokens[perfil]).status_code == 403
        db.expire_all()
        depois = db.scalar(select(func.count()).select_from(Auditoria).where(Auditoria.acao == "acesso_negado",
                                                                             Auditoria.alvo == rota))
        assert depois == antes + 1


def test_rota_antiga_iss_delega_a_amostra(client, tokens, leitor, db):
    r = client.post("/api/conectores/iss/municipes/executar", headers=tokens["admin"])
    assert r.status_code == 200 and r.json()["tipo"] == cc.TIPO_AMOSTRA_ISS
    assert db.scalar(select(func.count()).select_from(Auditoria).where(
        Auditoria.acao == "amostra_conector_solicitada", Auditoria.alvo == "conector:iss")) >= 1


def test_rota_antiga_iss_409_concorrente(client, tokens, leitor, db):
    db.add(Execucao(tipo=cc.TIPO_AMOSTRA_ISS, sistema="ISS", status="em_andamento", usuario="outro-iss@aion.local"))
    db.commit()
    try:
        assert client.post("/api/conectores/iss/municipes/executar", headers=tokens["admin"]).status_code == 409
    finally:
        for e in db.scalars(select(Execucao).where(Execucao.usuario == "outro-iss@aion.local")):
            e.status = "falha"
        db.commit()


# ---------- Depois do merge do #7: catálogo (PB12), id numérico, prod, migração 0006 ----------
def _base(db, nome):
    from app.models.controle import BaseCatalogo
    return db.scalar(select(BaseCatalogo).where(BaseCatalogo.nome == nome))


def test_0006_iss_pronto_no_catalogo(db):
    b = _base(db, "ISS")
    assert b.status == "implementado" and b.conjunto == "iss" and b.sistemas == ["ISS"]


def test_banco_vem_do_catalogo_sem_variavel_do_conector(leitor, db, monkeypatch):
    monkeypatch.delenv("SQLSERVER_DATABASE_CEMITERIO", raising=False)
    c, fb = cc.config_do_conector("cemiterio", db)
    assert c.database == "Cemiterio" and fb is False  # PB12: nome da base no catálogo
    c, fb = cc.config_do_conector("saude", db)
    assert c.database == "Saude" and fb is False       # override explícito por variável continua valendo


def test_amostra_pelo_id_numerico_da_base(client, tokens, leitor, db):
    b = _base(db, "Cemiterio")
    r = client.post(f"/api/conectores/{b.id}/amostra", headers=tokens["admin"])
    assert r.status_code == 200 and r.json()["tipo"] == cc.TIPO_AMOSTRA, r.text
    logs = " ".join(x.mensagem for x in db.scalars(select(Log).where(Log.execucao_id == r.json()["id"])))
    assert "catálogo" in logs
    bases = client.get("/api/conectores", headers=tokens["admin"]).json()["bases"]
    cem = next(x for x in bases if x["nome"] == "Cemiterio")
    assert cem["conector_id"] == "cemiterio" and cem["tem_amostra"] is True


@pytest.mark.parametrize("ambiente", ["prod", None])
def test_amostra_real_liberada_em_prod_e_sintetico_404(client, tokens, leitor, monkeypatch, ambiente):
    from app.core.config import get_settings
    if ambiente is None:
        monkeypatch.delenv("APP_ENV", raising=False)
    else:
        monkeypatch.setenv("APP_ENV", ambiente)
    get_settings.cache_clear()
    try:
        r = client.post("/api/conectores/iss/amostra", headers=tokens["admin"])
        assert r.status_code == 200, r.text
        d = client.get("/api/conectores", headers=tokens["admin"]).json()
        assert d["carga_habilitada"] is False
        iss = next(x for x in d["bases"] if x["nome"] == "ISS")
        assert iss["pode_carregar"] is True
        assert client.post(f"/api/conectores/{iss['id']}/executar", headers=tokens["admin"]).status_code == 404
        assert client.post("/api/conectores/demo/executar", headers=tokens["admin"]).status_code == 404
    finally:
        monkeypatch.setenv("APP_ENV", "test")
        get_settings.cache_clear()


def test_0006_downgrade_e_upgrade_idempotentes(db):
    from alembic import command

    from tests.conftest import alembic_cfg
    cfg = alembic_cfg()
    command.downgrade(cfg, "0005")
    db.expire_all()
    b = _base(db, "ISS")
    assert b.status == "mapeado" and b.conjunto is None
    command.upgrade(cfg, "head")
    command.upgrade(cfg, "head")  # sem efeito na segunda vez
    db.expire_all()
    assert _base(db, "ISS").status == "implementado"
