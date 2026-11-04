"""Cubos e cruzamentos: normalização de bairro, construção, consulta com lista fechada, supressão e achados."""
from datetime import date, timedelta

import pytest
from sqlalchemy import text


@pytest.fixture(scope="module")
def dados(client):
    from app.core.db import SessionLocal
    from app.gold import cruzamentos, cubos
    db = SessionLocal()
    for t in ("escola_matriculas", "escola_alunos", "empresas_sil", "dividas", "lancamentos", "imovel_possuidores", "imoveis", "vacinacoes",
              "agendamentos", "atendimentos", "unidades_saude", "enderecos"):
        db.execute(text(f"DELETE FROM silver.{t}"))
    db.execute(text("DELETE FROM ref.geral_bairro"))
    ins = lambda sql, linhas: db.execute(text(sql), linhas)  # noqa: E731
    ins("INSERT INTO ref.geral_bairro (cd_bai, bairro) VALUES (:c, :b)", [{"c": 1, "b": "JD. Centro"}, {"c": 2, "b": "Vila Nova"}])
    hoje = date.today()
    pessoas = [{"id": 900000 + i, "nome": f"Pessoa {i}", "nasc": date(1950, 1, 1) if i < 200 else date(2015, 1, 1), "fal": i < 6} for i in range(300)]
    ins("INSERT INTO silver.cidadaos (cidadao_id, nome, data_nascimento, falecido) VALUES (:id, :nome, :nasc, :fal)", pessoas)
    ins("""INSERT INTO silver.enderecos (cidadao_id, bairro, sistema, mais_recente) VALUES (:id, :b, 'T', true)""",
        [{"id": p["id"], "b": "Jardim Centro" if p["id"] % 2 else "VILA NOVA"} for p in pessoas])
    ins("INSERT INTO silver.unidades_saude (codigo, nome, ativa, cd_bairro) VALUES (1, 'UBS Centro', true, 1)", [{}])
    ins("INSERT INTO silver.atendimentos (chave_origem, cidadao_id, data, unidade) VALUES (:c, :id, :d, 1)",
        [{"c": f"t{p['id']}", "id": p["id"], "d": hoje - timedelta(days=30)} for p in pessoas[100:]])
    ins("INSERT INTO silver.imoveis (codi, cd_bairro, iptu_anual) VALUES (:c, :b, 100)", [{"c": 800000 + i, "b": 1 + i % 2} for i in range(80)])
    ins("""INSERT INTO silver.imovel_possuidores (codi, documento, tipo_pessoa, cidadao_id, ativo)
           VALUES (:c, :d, 'PF', :id, true)""", [{"c": 800000 + i, "d": f"doc{i}", "id": 900000 + i} for i in range(6)])
    db.commit()
    r = cubos.reconstruir(db)
    cruzamentos._cache.clear()
    yield db, r
    db.close()


def test_norm_bairro(banco):
    from app.core.db import SessionLocal
    db = SessionLocal()
    f = lambda v: db.scalar(text("SELECT gold.norm_bairro(:v)"), {"v": v})  # noqa: E731
    assert f("JD. São José") == f("Jardim Sao Jose") == "JARDIM SAO JOSE"
    assert f("Pq. das Águas") == "PARQUE DAS AGUAS"
    assert f("  ") is None and f("não informado") is None and f(None) is None
    db.close()


def test_antes_do_primeiro_calculo_a_tela_diz_nao_calculado(client, tokens):
    r = client.get("/api/cruzamentos", headers=tokens["leitura"])
    assert r.status_code == 200 and r.json()["calculado"] is False


def test_bairro_junta_grafias_e_id_e_estavel(dados):
    from app.gold import cubos
    db, _ = dados
    nomes = {n for n, in db.execute(text("SELECT norm FROM gold.dim_bairro"))}
    assert "JARDIM CENTRO" in nomes and "JD CENTRO" not in nomes
    antes = dict(db.execute(text("SELECT norm, id FROM gold.dim_bairro")).all())
    cubos.reconstruir(db)
    assert antes == dict(db.execute(text("SELECT norm, id FROM gold.dim_bairro")).all())
    assert db.scalar(text("SELECT to_regclass('gold.cubo_saude__novo')")) is None


def test_consulta_soma_e_suprime(client, tokens, dados):
    h = tokens["leitura"]
    r = client.get("/api/cubos/pessoas?agrupar=bairro&f_falecido=false", headers=h).json()["linhas"]
    por = {x["bairro"]: x["pessoas"] for x in r}
    assert por["Jardim Centro"] + por["Vila Nova"] == 294
    assert {"Jardim Centro", "Vila Nova"} <= {x["bairro"] for x in client.get("/api/cubos/imoveis?agrupar=bairro", headers=h).json()["linhas"]}
    # grupo com menos de 5 não sai
    db, _ = dados
    db.execute(text("INSERT INTO silver.imoveis (codi, cd_bairro, situacao) VALUES (:c, 1, 'RARA')"), [{"c": 810000 + i} for i in range(4)])
    db.commit()
    from app.gold import cubos
    cubos.reconstruir(db)
    r = client.get("/api/cubos/imoveis?agrupar=situacao", headers=h).json()
    assert "RARA" not in {x["situacao"] for x in r["linhas"]} and r["ocultos"] == 1 and r["calculado"]


def test_filtro_de_mes_e_nomes_de_tela(client, tokens, dados):
    h = tokens["leitura"]
    assert client.get("/api/cubos/imoveis?mes_ate=3", headers=h).status_code == 422
    assert client.get("/api/cubos/saude?mes_ate=3", headers=h).status_code == 200
    cat = client.get("/api/cubos", headers=h).json()["catalogo"]
    assert {"chave": "bairro", "nome": "Bairro"} in cat["saude"]["dimensoes"]
    assert {"chave": "media", "nome": "Remuneração média", "formato": "brl", "aditiva": False} in cat["rh"]["medidas"]


def test_cubo_gravado_sem_a_coluna_pede_recalculo(client, tokens, dados):
    db, _ = dados
    db.execute(text("ALTER TABLE gold.cubo_saude RENAME COLUMN vacina TO vacina_antiga"))
    db.commit()
    from app.gold import cubos_consulta
    cubos_consulta._cache.clear()
    r = client.get("/api/cubos/saude?agrupar=vacina", headers=tokens["leitura"])
    assert r.status_code == 200 and r.json()["calculado"] is False
    db.execute(text("ALTER TABLE gold.cubo_saude RENAME COLUMN vacina_antiga TO vacina"))
    db.commit()


@pytest.mark.parametrize("url", ["/api/cubos/nada", "/api/cubos/saude?agrupar=cidadao_id", "/api/cubos/saude?medidas=nome", "/api/cubos/saude?f_cidadao_id=1"])
def test_lista_fechada(client, tokens, url):
    assert client.get(url, headers=tokens["leitura"]).status_code == 422


def test_aux_de_pessoa_nao_e_consultavel(client, tokens):
    assert client.get("/api/cubos/aux_pessoa", headers=tokens["leitura"]).status_code == 422


def test_cruzamentos_sem_nome_de_pessoa(client, tokens, dados):
    r = client.get("/api/cruzamentos", headers=tokens["leitura"])
    assert r.status_code == 200
    d = r.json()
    assert d["calculado"] and {b["bairro"] for b in d["bairros"]} >= {"Jardim Centro", "Vila Nova"}
    assert "Pessoa 1" not in r.text
    ach = {a["id"]: a for a in d["cidade"]}
    assert ach["falecido_imovel"]["valor"] == 6
    assert ach["idoso_sem_consulta"]["valor"] > 0
    assert ach["imovel_sem_dono"]["valor"] >= 74


def test_destaques_e_correlacoes():
    from app.gold.cruzamentos import correlacoes, destaques
    linhas = [{"bairro": f"B{i}", "inadimplencia": 10.0, "consultas_mil": 100.0 + i, "vacinas_mil": 200.0 + 2 * i} for i in range(20)]
    linhas[0]["inadimplencia"] = 90.0
    d = destaques(linhas)
    assert d[0]["bairro"] == "B0" and d[0]["medida"] == "inadimplencia" and d[0]["sinal"] == "ruim"
    c = correlacoes(linhas)
    assert any({x["a"], x["b"]} == {"consultas_mil", "vacinas_mil"} and x["r"] == 1.0 for x in c)
    assert correlacoes(linhas[:5]) == []  # poucos bairros: nada de correlação


def test_recalcular_so_admin_e_em_fundo(client, tokens, dados, monkeypatch):
    from app.gold import cubos
    monkeypatch.setattr(cubos, "disparar", cubos.rodar)
    assert client.post("/api/cubos/recalcular", headers=tokens["leitura"]).status_code == 403
    r = client.post("/api/cubos/recalcular", headers=tokens["admin"])
    assert r.status_code == 202
    e = client.get("/api/cubos", headers=tokens["leitura"]).json()
    assert e["ultima_execucao"]["status"] == "sucesso" and e["em_andamento"] is None
    assert any(t["cubo"] == "cubo_saude" for t in e["tabelas"]) and "saude" in e["catalogo"]


def test_agenda_recalcula(client, tokens, dados):
    from app.services import agenda as ag
    assert ag.cubos_recusa(dados[0]) is None
    assert ag._nome(ag.CUBOS) == ag.NOME_CUBOS
