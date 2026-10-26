"""A13/A14/A15 com a fixture sintética FX01-FX11 e as contagens de expected_counts.json."""
import json
import pathlib
import re

import pytest
from sqlalchemy import create_engine, text

from tests.conftest import TEST_URL

ESPERADO = json.loads((pathlib.Path(__file__).parent / "fixtures" / "expected_counts.json").read_text())
CPF_COMPLETO = re.compile(r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b")
TABELAS = {"bronze_registros": "bronze.registros", "silver_cidadaos": "silver.cidadaos",
           "silver_identificadores": "silver.identificadores", "silver_vinculos": "silver.vinculos",
           "silver_enderecos": "silver.enderecos", "silver_correlacoes_pendentes": "silver.correlacoes_pendentes",
           "silver_inconsistencias": "silver.inconsistencias", "gold_cidadao_sistemas": "gold.cidadao_sistemas"}


def _contagens():
    eng = create_engine(TEST_URL)
    with eng.connect() as c:
        return {k: c.execute(text(f"select count(*) from {t}")).scalar() for k, t in TABELAS.items()}


def _q(sql, **p):
    eng = create_engine(TEST_URL)
    with eng.connect() as c:
        return c.execute(text(sql), p).all()


def test_expected_counts_reproduzivel():
    from tests.fixtures.gerar_fixture import calcula
    assert calcula() == ESPERADO


@pytest.fixture(scope="module")
def demo(client, tokens):
    assert client.get("/api/dashboard", headers=tokens["leitura"]).status_code == 200  # vazio não quebra
    r1 = client.post("/api/conectores/demo/executar", headers=tokens["operador"])
    assert r1.status_code == 200, r1.text
    c1 = _contagens()
    r2 = client.post("/api/conectores/demo/executar", headers=tokens["operador"])
    return r1.json(), r2.json(), c1, _contagens()


def test_demo_contagens_iguais_ao_esperado(demo):
    r1, _, c1, _ = demo
    esp = ESPERADO["amostra_100"]
    assert {k: c1[k] for k in TABELAS} == {k: esp[k] for k in TABELAS}
    assert r1["status"] == "sucesso" and r1["lidos"] == esp["lidos"] and r1["novos"] == esp["bronze_registros"]


def test_idempotencia_sem_novidades(demo):
    _, r2, c1, c2 = demo
    assert c1 == c2
    assert r2["status"] == "sem_novidades" and r2["novos"] == 0 and r2["atualizados"] == 0
    st = _q("select status, novos from controle.execucoes order by id desc limit 1")[0]
    assert tuple(st) == ("sem_novidades", 0)


def test_bronze_metadados_e_hash(demo):
    from app.bronze.ingestao import hash_registro
    for sis, idor, extr, h, ex, tipo, dados in _q(
            "select sistema_origem, id_origem, extraido_em, hash, execucao_id, jsonb_typeof(dados), dados "
            "from bronze.registros limit 5"):
        assert sis and idor and extr and ex and tipo == "object" and hash_registro(dados) == h


def test_fx01_mesmo_cpf_com_e_sem_mascara(demo, client, tokens):
    cid = _q("select cidadao_id from silver.cidadaos where nome='Ana Teste Silva'")
    assert len(cid) == 1
    r = client.get(f"/api/gold/cidadaos/{cid[0][0]}/sistemas", headers=tokens["leitura"]).json()
    assert r["sistemas"] == ["DEMO_CEMITERIO", "DEMO_SAUDE"]


def test_fx02_endereco_mais_recente(demo, client, tokens):
    cid = _q("select cidadao_id from silver.cidadaos where nome='Bruno Exemplo Souza'")[0][0]
    ends = client.get(f"/api/gold/cidadaos/{cid}", headers=tokens["leitura"]).json()["enderecos"]
    assert len(ends) == 2
    rec = [e for e in ends if e["mais_recente"]]
    assert len(rec) == 1 and rec[0]["valor"].startswith("Rua Teste 2") and rec[0]["atualizado_em"].startswith("2026-03-05")


def test_fx04_gemeos_distintos(demo):
    assert len(_q("select 1 from silver.cidadaos where nome in ('Diego Teste Rocha','Davi Teste Rocha')")) == 2


def test_fx06_revisao_sem_vinculo(demo):
    p = _q("select motivo from silver.correlacoes_pendentes where nome='Fábio Teste Nunes'")
    assert [x[0] for x in p] == ["revisao"]


def test_fx07_fx08_cpf_invalido(demo):
    assert len(_q("select 1 from silver.inconsistencias where tipo='cpf_invalido'")) == 2


def test_fx09_duplicata(demo):
    assert len(_q("select 1 from bronze.registros where dados->>'nome'='Íris Ficticia Melo'")) == 1


def test_fx10_ambiguo_nao_funde(demo):
    assert [x[0] for x in _q("select motivo from silver.correlacoes_pendentes where nome='João Modelo Dias'")] == ["ambiguo"]
    assert _q("select nome from silver.cidadaos where nome='João Modelo Dias'") == []


def test_ambiguos_sem_cidadao_id(demo):
    assert _q("select count(*) from silver.vinculos v join silver.correlacoes_pendentes p "
              "on (v.sistema, v.tabela, v.chave_origem) = (p.sistema, p.tabela, p.chave_origem)")[0][0] == 0


def test_cpf_mascarado_em_toda_saida(demo, client, tokens):
    h = tokens["operador"]
    cid = _q("select cidadao_id from silver.cidadaos where nome='Ana Teste Silva'")[0][0]
    for url in ("/api/silver/cidadaos", "/api/silver/cidadaos.csv", "/api/silver/inconsistencias",
                "/api/silver/pendencias", f"/api/gold/cidadaos/{cid}", "/api/dashboard", "/api/conectores"):
        r = client.get(url, headers=h)
        assert r.status_code == 200 and not CPF_COMPLETO.search(r.text), url
    assert "***." in client.get("/api/silver/cidadaos", headers=h).text


def test_busca_por_cpf_completo_responde_mascarado(demo, client, tokens):
    cpf = _q("select valor from silver.identificadores i join silver.cidadaos c using (cidadao_id) "
             "where c.nome='Ana Teste Silva'")[0][0]
    r = client.get("/api/silver/cidadaos", params={"busca": cpf}, headers=tokens["leitura"])
    assert [x["nome"] for x in r.json()] == ["Ana Teste Silva"] and cpf not in r.text


def test_revelar_cpf(demo, client, tokens):
    cid = _q("select cidadao_id from silver.cidadaos where nome='Ana Teste Silva'")[0][0]
    url = f"/api/cidadaos/{cid}/revelar-cpf"
    for perfil in ("admin", "operador", "leitura"):
        assert client.post(url, json={"motivo": "Atendimento ao cidadão"}, headers=tokens[perfil]).status_code == 403
    assert _q("select count(*) from controle.auditoria where acao='revelar_cpf_negado'")[0][0] >= 3
    r = client.post(url, json={}, headers=tokens["admin_lgpd"])
    assert r.status_code == 400 and r.json()["detail"] == "Informe o motivo para revelar o CPF."
    assert client.post(url, json={"motivo": "curto"}, headers=tokens["admin_lgpd"]).status_code == 400
    antes = _q("select count(*) from controle.auditoria where acao='revelar_cpf'")[0][0]
    for _ in range(2):
        r = client.post(url, json={"motivo": "Solicitação formal do titular"}, headers=tokens["admin_lgpd"])
        assert r.status_code == 200 and len(r.json()["cpf"]) == 11 and r.json()["remascarar_em_segundos"] == 60
    linhas = _q("select usuario, alvo, detalhe->>'motivo' from controle.auditoria where acao='revelar_cpf' order by id")
    assert len(linhas) == antes + 2 and tuple(linhas[-1]) == ("admin_lgpd@aion.local", f"cidadao:{cid}",
                                                              "Solicitação formal do titular")


def test_carga_completa_so_admin(demo, client, tokens):
    r = client.post("/api/conectores/demo/executar", json={"completa": True}, headers=tokens["operador"])
    assert r.status_code == 403


def test_auditoria_de_carga(demo):
    a = _q("select usuario, alvo from controle.auditoria where acao='carga_executada' order by id desc limit 1")[0]
    assert a.usuario == "operador@aion.local" and a.alvo.startswith("execucao:")


def test_retomada_apos_falha_sem_duplicar(demo, db, monkeypatch):
    """Falha no 3º lote; a reexecução retoma sem duplicar e chega ao esperado (carga completa)."""
    from app.core.config import get_settings
    from app.services.pipeline import executar_demo
    monkeypatch.setattr(get_settings(), "lote_tamanho", 50)

    def falha(n):
        if n == 3:
            raise RuntimeError("falha simulada")
    ex = executar_demo(db, "teste", completa=True, antes_do_lote=falha)
    assert ex.status == "falha"
    ex2 = executar_demo(db, "teste", completa=True)
    assert ex2.status == "sucesso" and ex2.lotes >= 10
    c = _contagens()
    esp = ESPERADO["completa"]
    assert {k: c[k] for k in TABELAS} == {k: esp[k] for k in TABELAS}
    ex3 = executar_demo(db, "teste", completa=True)
    assert ex3.status == "sem_novidades" and _contagens() == c
