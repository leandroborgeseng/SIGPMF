"""Catálogo de bases + indicador por camada (GET /api/conectores, POST /api/conectores/{id}/executar).
Só dados sintéticos (OrigemMock); nenhum SQL Server real."""
import pytest
from sqlalchemy import create_engine, text

from app.catalogo.bases import BASES, semeia
from tests.conftest import TEST_URL


def _q(sql, **p):
    eng = create_engine(TEST_URL)
    with eng.begin() as c:
        r = c.execute(text(sql), p)
        return r.all() if r.returns_rows else []


def _bases(client, h):
    r = client.get("/api/conectores", headers=h)
    assert r.status_code == 200, r.text
    return {b["nome"]: b for b in r.json()["bases"]}


def _id(client, h, nome):
    return _bases(client, h)[nome]["id"]


CAMADAS = ("gold.cidadao_dados", "gold.cidadao_sistemas", "silver.enderecos", "silver.vinculos", "silver.identificadores",
              "silver.correlacoes_pendentes", "silver.inconsistencias", "silver.cidadaos", "bronze.registros")


def _limpa():
    for t in CAMADAS:
        _q(f"delete from {t}")


@pytest.fixture(scope="module")
def carregado(client, tokens):
    _limpa()
    for nome in ("Saude", "Cemiterio"):
        r = client.post(f"/api/conectores/{_id(client, tokens['admin'], nome)}/executar", headers=tokens["admin"])
        assert r.status_code == 200, r.text
        assert r.json()["status"] in ("sucesso", "sem_novidades")
    yield _bases(client, tokens["leitura"])
    _limpa()  # não deixa camadas carregadas para os módulos seguintes


def test_catalogo_tem_todas_as_bases_da_triagem(client, tokens):
    b = _bases(client, tokens["leitura"])
    assert len(b) == len(BASES) == 73
    assert sum(1 for x in BASES if x[2] == "schema") == 38
    assert {n for n, x in b.items() if x["status"] == "implementado"} == {"Saude", "Cemiterio", "ISS"}  # ISS: 0006 (#8)
    fila = sorted((x["prioridade"], n) for n, x in b.items() if x["status"] == "mapeado")
    assert [n for _, n in fila] == ["CadastroUnico", "Protocolo_WEB", "Labor", "IPTU", "Divida"]
    for n in ("Senhas", "API_Usuario", "DBSENIOR", "LogSistemas", "Geral"):
        assert b[n]["status"] == "excluido"
    assert b["Alvara"]["prioridade"] and b["IntegracaoJuridicoDivida"]["prioridade"]


def test_seed_idempotente_sem_delete(db):
    antes = _q("select count(*), max(id) from controle.bases_catalogo")[0]
    with db.get_bind().begin() as c:
        semeia(c)
        semeia(c)
    assert _q("select count(*), max(id) from controle.bases_catalogo")[0] == antes


def test_seed_nao_altera_linha_existente(db):
    """Depois da 0006, semear de novo mantém o ISS implementado (seed só insere)."""
    with db.get_bind().begin() as c:
        c.exec_driver_sql("UPDATE controle.bases_catalogo SET descricao = 'editada' WHERE nome = 'Saude'")
        semeia(c)
    assert _q("select status, conjunto from controle.bases_catalogo where nome='ISS'")[0] == ("implementado", "iss")
    assert _q("select descricao from controle.bases_catalogo where nome='Saude'")[0][0] == "editada"


def test_contagens_batem_com_count(carregado):
    for nome, sis in (("Saude", "SAUDE"), ("Cemiterio", "CEMITERIO")):
        c = carregado[nome]["contagens"]
        assert c["bronze"] == _q("select count(*) from bronze.registros where sistema_origem=:s", s=sis)[0][0] > 0
        assert c["prata"] == _q("select count(*) from silver.vinculos where sistema=:s", s=sis)[0][0]
        assert c["ouro"] == _q("select count(distinct cidadao_id) from gold.cidadao_dados where sistema=:s", s=sis)[0][0]
        assert c["pendentes"] == _q("select count(*) from silver.correlacoes_pendentes where sistema=:s and status='pendente'",
                                    s=sis)[0][0]
        assert carregado[nome]["ultima_carga"]["tipo"] == "carga_demo_amostra"
    assert carregado["Saude"]["contagens"]["prata"] > 0


def test_ouro_conta_cidadaos_unicos(carregado):
    c = carregado["Saude"]["contagens"]
    linhas = _q("select count(*) from gold.cidadao_dados where sistema='SAUDE'")[0][0]
    unicos = _q("select count(distinct cidadao_id) from gold.cidadao_dados where sistema='SAUDE'")[0][0]
    assert c["ouro"] == unicos < linhas  # ouro = cidadãos únicos, não linhas de cidadao_dados

def test_base_sem_conector_zera(carregado):
    for nome in ("CadastroUnico", "Senhas", "Bibliotecario"):
        assert carregado[nome]["contagens"] == {"bronze": 0, "prata": 0, "ouro": 0, "pendentes": 0}
        assert carregado[nome]["ultima_carga"] is None
        assert carregado[nome]["tem_conector"] is False


def test_reprocessar_nao_duplica(client, tokens, carregado):
    sid = carregado["Saude"]["id"]
    assert client.post(f"/api/conectores/{sid}/executar", headers=tokens["admin"]).status_code == 200
    assert _bases(client, tokens["leitura"])["Saude"]["contagens"] == carregado["Saude"]["contagens"]


def test_auth_e_perfis(client, tokens):
    assert client.get("/api/conectores").status_code == 401
    for p in ("admin", "admin_lgpd", "operador", "leitura"):
        assert client.get("/api/conectores", headers=tokens[p]).status_code == 200
    sid = _id(client, tokens["admin"], "Saude")
    assert client.post(f"/api/conectores/{sid}/executar").status_code == 401
    for p in ("admin_lgpd", "operador", "leitura"):
        assert client.post(f"/api/conectores/{sid}/executar", headers=tokens[p]).status_code == 403


def test_nao_implementado_409_excluido_422_inexistente_404(client, tokens):
    h = tokens["admin"]
    n0 = _q("select count(*) from controle.execucoes")[0][0]
    assert client.post(f"/api/conectores/{_id(client, h, 'CadastroUnico')}/executar", headers=h).status_code == 409
    assert client.post(f"/api/conectores/{_id(client, h, 'Senhas')}/executar", headers=h).status_code == 422
    assert client.post("/api/conectores/999999/executar", headers=h).status_code == 404
    assert _q("select count(*) from controle.execucoes")[0][0] == n0
    assert _q("select count(*) from controle.auditoria where acao='executar_conector_negado'")[0][0] >= 2


def test_execucao_auditada(client, tokens, carregado):
    assert _q("select count(*) from controle.auditoria where acao='executar_conector' and alvo='base:Saude'")[0][0] >= 1


def test_totais_e_bloqueios(client, tokens, carregado):
    d = client.get("/api/conectores", headers=tokens["leitura"]).json()
    for k in ("bronze", "prata", "ouro", "pendentes"):
        assert d["totais"][k] == sum(b["contagens"][k] for b in d["bases"])
    b = {x["nome"]: x for x in d["bases"]}
    assert b["Saude"]["pode_carregar"] and b["Saude"]["tem_amostra"] and not b["Saude"]["com_erro"]
    assert b["CadastroUnico"]["motivo_bloqueio"] == "Sem lista de colunas aprovada"
    assert b["Senhas"]["motivo_bloqueio"] == "Base descartada na triagem"
    assert b["Bibliotecario"]["motivo_bloqueio"] == "Conector ainda não implementado"


def test_completa_exige_amostra_e_reprocessar_nao_duplica(client, tokens, carregado):
    h = tokens["admin"]
    sid = carregado["Cemiterio"]["id"]
    _q("update controle.execucoes set tipo='x_teste' where tipo='carga_demo_amostra' and sistema='CEMITERIO'")
    try:
        assert client.post(f"/api/conectores/{sid}/executar", json={"modo": "completa"}, headers=h).status_code == 409
    finally:
        _q("update controle.execucoes set tipo='carga_demo_amostra' where tipo='x_teste'")
    r = client.post(f"/api/conectores/{sid}/executar", json={"modo": "completa"}, headers=h)
    assert r.status_code == 200 and r.json()["finalizado_em"]
    antes = _bases(client, tokens["leitura"])
    r = client.post(f"/api/conectores/{sid}/executar", json={"modo": "reprocessar"}, headers=h)
    assert r.status_code == 200 and r.json()["status"] == "sucesso"
    depois = _bases(client, tokens["leitura"])
    assert {n: x["contagens"] for n, x in depois.items()} == {n: x["contagens"] for n, x in antes.items()}
    assert depois["Cemiterio"]["ultima_carga"]["tipo"] == "reprocessamento"
    assert client.post(f"/api/conectores/{sid}/executar", json={"modo": "x"}, headers=h).status_code == 422


@pytest.mark.parametrize("env", ["prod", "production", "development", "homolog", ""])
def test_executar_404_fora_de_dev(client, tokens, monkeypatch, env):
    from app.core.config import get_settings
    monkeypatch.setattr(get_settings(), "app_env", env)
    h = tokens["admin"]
    sid = _id(client, h, "Saude")
    n0 = _q("select count(*) from controle.execucoes")[0][0]
    for corpo in ({"modo": "amostra"}, {"modo": "completa"}, {"modo": "reprocessar"}):
        assert client.post(f"/api/conectores/{sid}/executar", json=corpo, headers=h).status_code == 404
    assert _q("select count(*) from controle.execucoes")[0][0] == n0
    assert client.get("/api/conectores", headers=h).json()["carga_habilitada"] is False


@pytest.mark.parametrize("env", ["dev", "test"])
def test_executar_funciona_em_dev(client, tokens, monkeypatch, env):
    from app.core.config import get_settings
    monkeypatch.setattr(get_settings(), "app_env", env)
    h = tokens["admin"]
    assert client.get("/api/conectores", headers=h).json()["carga_habilitada"] is True
    r = client.post(f"/api/conectores/{_id(client, h, 'Saude')}/executar", headers=h)
    assert r.status_code == 200, r.text
    _limpa()


def test_403_de_perfil_auditado(client, tokens):
    sid = _id(client, tokens["admin"], "Saude")
    n0 = _q("select count(*) from controle.execucoes")[0][0]
    for p in ("admin_lgpd", "operador", "leitura"):
        antes = _q("select count(*) from controle.auditoria where acao='executar_conector_negado' and usuario=:u",
                   u=f"{p}@aion.local")[0][0]
        assert client.post(f"/api/conectores/{sid}/executar", headers=tokens[p]).status_code == 403
        rows = _q("select alvo, detalhe from controle.auditoria where acao='executar_conector_negado' and usuario=:u "
                  "order by id", u=f"{p}@aion.local")
        assert len(rows) == antes + 1
        assert rows[-1][0] == f"base_id:{sid}" and rows[-1][1]["motivo"] == "perfil" and rows[-1][1]["perfil"] == p
    assert _q("select count(*) from controle.execucoes")[0][0] == n0



@pytest.mark.parametrize("env", ["prod", "development", ""])
def test_demo_executar_404_fora_de_dev(client, tokens, monkeypatch, env):
    from app.core.config import get_settings
    monkeypatch.setattr(get_settings(), "app_env", env)
    n0 = _q("select count(*) from controle.execucoes")[0][0]
    for p in ("admin", "operador"):
        for corpo in ({}, {"completa": True}, {"conjunto": "saude"}):
            assert client.post("/api/conectores/demo/executar", json=corpo, headers=tokens[p]).status_code == 404
    assert _q("select count(*) from controle.execucoes")[0][0] == n0


def test_app_env_ausente_e_producao(monkeypatch):
    from app.api.rotas import carga_habilitada
    from app.core.config import Settings, get_settings
    monkeypatch.delenv("APP_ENV", raising=False)
    s = Settings()  # type: ignore[call-arg]
    assert s.app_env == "prod"
    monkeypatch.setattr(get_settings(), "app_env", s.app_env)
    assert carga_habilitada() is False
