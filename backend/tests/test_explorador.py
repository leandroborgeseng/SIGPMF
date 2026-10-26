"""EX1–EX7: explorador de camadas. Somente dados SINTÉTICOS (sistema fictício EXSINT)."""
import logging
import uuid

import pytest
from sqlalchemy import text

from app.explorador.mascara import mascara_campo, tipo_sensivel
from app.identity.cpf import digitos_verificadores

S = "EXSINT"
CPF = "529982247" + digitos_verificadores("529982247")  # CPF sintético (exemplo clássico de DV)
CNPJ = "11222333000181"
CNS = "898001160660990"
NOME, MAE = "Fulano Sintetico Teste", "Beltrana Sintetica Teste"
END, TEL, EMAIL = "Rua Ficticia 123", "16999887766", "fulano.sintetico@exemplo.test"
NASC, OBITO = "1971-03-14", "2026-01-10"
MOT = "Conferência LGPD chamado 123"
PII = (CPF, CNPJ, CNS, NOME, MAE, END, TEL, EMAIL, NASC, OBITO, "Fulano", "Beltrana", "Ficticia", "999887766", "fulano.sintetico")
CID = str(uuid.uuid5(uuid.NAMESPACE_DNS, "exsint-1"))
ROTAS = ("conectores", "tabelas", "perfil", "amostra", "funil", "linhagem", "revelar")
BASE = {"conector": S, "camada": "bronze", "tabela": "Pacientes"}


@pytest.fixture(scope="module")
def dados(client):
    from app.core.db import SessionLocal
    db = SessionLocal()
    eid = db.execute(text("INSERT INTO controle.execucoes (tipo, sistema, status, finalizado_em) VALUES "
                          "('carga_demo_amostra', :s, 'sucesso', now()) RETURNING id"), {"s": S}).scalar()
    ids = []
    for i in range(120):  # 120 linhas -> 3 páginas de 50
        d = {"id": i, "cpf": CPF if i == 0 else None, "cnpj": CNPJ if i == 0 else None, "cns": CNS if i == 0 else None,
             "nome": NOME if i == 0 else f"Pessoa {i:03d} Exemplo", "nome_mae": MAE, "data_nascimento": NASC, "endereco": END, "telefone": TEL,
             "email": EMAIL, "obs": f"texto livre cpf {CPF}" if i == 1 else "nada", "grupo": "A" if i % 2 else "B"}
        ids.append(db.execute(text("INSERT INTO bronze.registros (sistema_origem, tabela, id_origem, dados, hash, execucao_id) "
                                   "VALUES (:s, 'Pacientes', :io, CAST(:d AS jsonb), :h, :e) RETURNING id"),
                              {"s": S, "io": f"P{i:03d}", "d": __import__("json").dumps(d), "h": f"h{i}", "e": eid}).scalar())
    db.execute(text("INSERT INTO silver.cidadaos (cidadao_id, nome) VALUES (:c, :n)"), {"c": CID, "n": NOME})
    db.execute(text("INSERT INTO silver.identificadores (cidadao_id, tipo, valor) VALUES (:c, 'cpf', :v)"), {"c": CID, "v": CPF})
    db.execute(text("INSERT INTO silver.vinculos (cidadao_id, sistema, tabela, chave_origem, bronze_id, metodo, confianca, atributos) "
                    "VALUES (:c, :s, 'Pacientes', 'P000', :b, 'cpf', 1.0, '[]')"), {"c": CID, "s": S, "b": ids[0]})
    db.execute(text("INSERT INTO silver.correlacoes_pendentes (sistema, tabela, chave_origem, bronze_id, motivo, nome, candidatos, status) "
                    "VALUES (:s, 'Pacientes', 'P005', :b, 'sem_correspondencia', :n, '[]', 'pendente')"), {"s": S, "b": ids[5], "n": NOME})
    db.execute(text("INSERT INTO gold.cidadao_dados (cidadao_id, dominio, campo, valor, sistema, mais_recente) VALUES "
                    "(:c, 'saude', 'cpf', :v, :s, true), (:c, 'saude', 'unidade', 'UBS Central', :s, true), "
                    "(:c, 'identificacao', 'data_obito', :o, :s, true), (:c, 'identificacao', 'falecido', 'sim', :s, true)"),
               {"c": CID, "v": CPF, "s": S, "o": OBITO})
    db.commit()
    db.close()
    return ids


def _sem_pii(txt: str):
    for p in PII:
        assert p not in txt, f"dado pessoal sintético vazou: {p[:4]}…"


# ---------- EX6: 403 ----------
@pytest.mark.parametrize("perfil", ["operador", "leitura"])
@pytest.mark.parametrize("rota", ROTAS)
def test_ex6_nao_admin_403_e_auditado(client, tokens, dados, db, perfil, rota):
    antes = db.execute(text("SELECT count(*) FROM controle.auditoria WHERE acao='explorar_acesso_negado'")).scalar()
    r = client.post(f"/api/explorar/{rota}", json={**BASE, "cidadao_id": CID}, headers=tokens[perfil])
    assert r.status_code == 403
    db.expire_all()
    assert db.execute(text("SELECT count(*) FROM controle.auditoria WHERE acao='explorar_acesso_negado'")).scalar() == antes + 1


@pytest.mark.parametrize("rota", ROTAS)
def test_ex6_sem_login_401(client, rota):
    assert client.post(f"/api/explorar/{rota}", json=BASE).status_code == 401


@pytest.mark.parametrize("rota", ROTAS)
def test_ex6_so_post_get_405(client, tokens, rota):
    assert client.get(f"/api/explorar/{rota}", headers=tokens["admin"]).status_code == 405


def test_ex6_toda_acesso_auditado_sem_valor_de_filtro(client, tokens, dados, db):
    r = client.post("/api/explorar/amostra", json={**BASE, "filtros": {"cpf": CPF, "nome": NOME}, "motivo_filtro": MOT},
                    headers=tokens["admin_lgpd"])
    assert r.status_code == 200
    db.expire_all()
    for acao in ("explorar_amostra", "explorar_filtro_pessoal"):
        det = db.execute(text("SELECT usuario || alvo || detalhe::text FROM controle.auditoria WHERE acao=:a ORDER BY id DESC LIMIT 1"),
                         {"a": acao}).scalar()
        assert "cpf" in det and "nome" in det and "admin_lgpd@aion.local" in det
        _sem_pii(det)
    assert "chamado 123" in det and "Pacientes" in det


def test_ex6_sem_pii_em_log_nem_url(client, tokens, dados, caplog):
    caplog.set_level(logging.DEBUG)
    urls = []
    for rota, corpo in (("conectores", {}), ("tabelas", BASE), ("perfil", BASE), ("funil", BASE),
                        ("amostra", {**BASE, "filtros": {"cpf": CPF, "email": EMAIL}, "motivo_filtro": MOT}),
                        ("linhagem", {"cidadao_id": CID})):
        r = client.post(f"/api/explorar/{rota}", json=corpo, headers=tokens["admin_lgpd"])
        assert r.status_code == 200, (rota, r.text)
        urls.append(str(r.request.url))
        _sem_pii(r.text)
    _sem_pii(" ".join(urls))
    _sem_pii(caplog.text)


# ---------- EX1 ----------
def test_ex1_conectores_tabelas_e_perfil(client, tokens, dados):
    h = tokens["admin"]
    cs = client.post("/api/explorar/conectores", json={}, headers=h).json()
    assert S in [c["conector"] for c in cs["conectores"]] and cs["pode_revelar"] is False
    t = client.post("/api/explorar/tabelas", json=BASE, headers=h).json()
    assert t["tabelas"][0] == {"tabela": "Pacientes", "linhas": 120, "ultima_carga": t["tabelas"][0]["ultima_carga"]}
    assert t["tabelas"][0]["ultima_carga"]
    p = client.post("/api/explorar/perfil", json=BASE, headers=h).json()
    cols = {c["coluna"]: c for c in p["colunas"]}
    assert cols["cpf"]["preenchido_pct"] == pytest.approx(0.8, abs=0.1) and cols["cpf"]["distintos"] == 1
    assert cols["grupo"]["distintos"] == 2 and cols["grupo"]["preenchido_pct"] == 100.0
    assert len(cols["nome"]["exemplos"]) == 3 and cols["nome"]["sensivel"]
    assert cols["cpf"]["exemplos"] == ["***.982.247-**"]
    assert cols["email"]["exemplos"] == ["f***@exemplo.test"]
    assert cols["cns"]["exemplos"][0] != CNS and cols["cnpj"]["exemplos"][0] != CNPJ
    _sem_pii(str(p))


def test_ex1_prata_ouro_listam_e_perfil_mascarado(client, tokens, dados):
    h = tokens["admin"]
    pr = client.post("/api/explorar/tabelas", json={**BASE, "camada": "prata"}, headers=h).json()
    n = {t["tabela"]: t["linhas"] for t in pr["tabelas"]}
    assert n["vinculos"] == 1 and n["cidadaos"] == 1 and n["identificadores"] == 1 and "municipes" not in n
    for camada, tab in (("prata", "identificadores"), ("prata", "cidadaos"), ("ouro", "cidadao_dados")):
        p = client.post("/api/explorar/perfil", json={**BASE, "camada": camada, "tabela": tab}, headers=h)
        assert p.status_code == 200
        _sem_pii(p.text)


def test_ex7_camada_vazia_e_processando(client, tokens, dados, db):
    from app.core.db import SessionLocal
    s = SessionLocal()
    s.execute(text("INSERT INTO controle.execucoes (tipo, sistema, status) VALUES ('x', 'EXVAZIO', 'em_andamento')"))
    s.execute(text("INSERT INTO bronze.registros (sistema_origem, tabela, id_origem, dados, hash, execucao_id) "
                   "SELECT 'EXVAZIO', 'T', '1', '{}'::jsonb, 'h', max(id) FROM controle.execucoes"))
    s.commit()
    s.close()
    r = client.post("/api/explorar/tabelas", json={"conector": "EXVAZIO", "camada": "ouro"}, headers=tokens["admin"]).json()
    assert r["vazia"] and r["mensagem_vazia"] == "Camada ainda não processada"
    assert r["estado"]["processando"] and r["estado"]["mensagem"].startswith("Processando…")


# ---------- EX2 ----------
def test_ex2_paginacao_50(client, tokens, dados):
    h = tokens["admin"]
    vistos = []
    for pag, n, prox in ((1, 50, True), (2, 50, True), (3, 20, False)):
        r = client.post("/api/explorar/amostra", json={**BASE, "pagina": pag}, headers=h).json()
        assert len(r["linhas"]) == n and r["tem_proxima"] is prox and r["tem_anterior"] is (pag > 1) and "total" not in r
        vistos += [x["id_origem"] for x in r["linhas"]]
    assert len(set(vistos)) == 120


def test_ex2_mascara_todas_colunas_pessoais(client, tokens, dados):
    r = client.post("/api/explorar/amostra", json=BASE, headers=tokens["admin"]).json()
    l0, l1 = r["linhas"][0], r["linhas"][1]
    assert l0["cpf"] == "***.982.247-**" and l0["nome"] == "Ful*** S*** T***" and l0["nome_mae"] == "Bel*** S*** T***"
    assert l0["data_nascimento"] == "**/**/****" and l0["telefone"] == "(16) ****-**66"
    assert l0["email"] == "f***@exemplo.test" and l0["telefone"].endswith("66") and "999" not in l0["telefone"]
    assert l0["endereco"] != END and l0["cns"] != CNS and l0["cnpj"] != CNPJ
    assert CPF not in l1["obs"]  # CPF em texto livre também é mascarado
    _sem_pii(str(r))
    for camada, tab in (("prata", "identificadores"), ("prata", "cidadaos"), ("ouro", "cidadao_dados")):
        x = client.post("/api/explorar/amostra", json={**BASE, "camada": camada, "tabela": tab}, headers=tokens["admin_lgpd"])
        assert x.status_code == 200 and x.json()["linhas"]
        _sem_pii(x.text)


def _n(client, h, filtros, **extra):
    r = client.post("/api/explorar/amostra", json={**BASE, "filtros": filtros, **extra}, headers=h)
    assert r.status_code == 200, r.text
    j = r.json()
    total = len(j["linhas"])
    while j["tem_proxima"]:
        j = client.post("/api/explorar/amostra", json={**BASE, "filtros": filtros, "pagina": j["pagina"] + 1, **extra}, headers=h).json()
        total += len(j["linhas"])
    return total


def test_ex2_filtro_por_post_igualdade_exata(client, tokens, dados):
    h, m = tokens["admin_lgpd"], {"motivo_filtro": MOT}  # P1: na bronze, filtro além de id é só do admin_lgpd
    assert _n(client, h, {"grupo": "A"}, **m) == 60
    assert _n(client, h, {"grupo": "a"}, **m) == 0  # exato, nunca LIKE/ILIKE
    assert _n(client, h, {"grupo": "%"}, **m) == 0
    assert _n(client, h, {"obs": "nada"}, **m) == 119
    assert _n(client, h, {"obs": "nad"}, **m) == 0
    assert _n(client, tokens["admin"], {"id": str(dados[3])}) == 1  # id continua livre para o admin comum


def test_ex2_filtro_coluna_pessoal_so_lgpd_com_motivo_auditado(client, tokens, dados, db):
    for perfil in ("admin",):
        r = client.post("/api/explorar/amostra", json={**BASE, "filtros": {"cpf": CPF}, "motivo_filtro": MOT}, headers=tokens[perfil])
        assert r.status_code == 403
    r = client.post("/api/explorar/amostra", json={**BASE, "camada": "ouro", "tabela": "cidadao_dados", "filtros": {"valor": CPF}},
                    headers=tokens["admin"])
    assert r.status_code == 403  # valor de chave/valor também é pessoal
    lg = tokens["admin_lgpd"]
    assert client.post("/api/explorar/amostra", json={**BASE, "filtros": {"cpf": CPF}}, headers=lg).status_code == 400
    assert client.post("/api/explorar/amostra", json={**BASE, "filtros": {"cpf": CPF}, "motivo_filtro": "curto"}, headers=lg).status_code == 400
    antes = db.execute(text("SELECT count(*) FROM controle.auditoria WHERE acao='explorar_filtro_pessoal'")).scalar()
    assert _n(client, lg, {"cpf": CPF}, motivo_filtro=MOT) == 1
    assert _n(client, lg, {"cpf": CPF[:5]}, motivo_filtro=MOT) == 0
    db.expire_all()
    assert db.execute(text("SELECT count(*) FROM controle.auditoria WHERE acao='explorar_filtro_pessoal'")).scalar() == antes + 2
    assert db.execute(text("SELECT count(*) FROM controle.auditoria WHERE acao='explorar_filtro_pessoal_negado'")).scalar() >= 2


def test_ex2_revelar_so_admin_lgpd_com_motivo(client, tokens, dados, db):
    corpo = {**BASE, "linha_id": dados[0], "coluna": "cpf", "motivo": MOT}
    sem = {k: v for k, v in corpo.items() if k != "motivo"}
    assert client.post("/api/explorar/revelar", json=sem, headers=tokens["admin_lgpd"]).status_code == 400
    antes = db.execute(text("SELECT count(*) FROM controle.auditoria WHERE acao='explorar_revelar'")).scalar()
    assert client.post("/api/explorar/revelar", json=corpo, headers=tokens["admin"]).status_code == 403
    assert client.post("/api/explorar/revelar", json={**corpo, "motivo": "curto"}, headers=tokens["admin_lgpd"]).status_code == 400
    r = client.post("/api/explorar/revelar", json=corpo, headers=tokens["admin_lgpd"])
    assert r.status_code == 200 and r.json() == {"valor": CPF, "remascarar_em_segundos": 60}
    assert client.post("/api/explorar/revelar", json=corpo, headers=tokens["admin_lgpd"]).status_code == 200
    db.expire_all()
    assert db.execute(text("SELECT count(*) FROM controle.auditoria WHERE acao='explorar_revelar'")).scalar() == antes + 2
    a = db.execute(text("SELECT alvo, detalhe::text FROM controle.auditoria WHERE acao='explorar_revelar' ORDER BY id DESC LIMIT 1")).one()
    assert "cpf" in a[0] and "chamado 123" in a[1] and CPF not in a[1]
    assert db.execute(text("SELECT count(*) FROM controle.auditoria WHERE acao='explorar_revelar_negado'")).scalar() >= 1
    assert client.post("/api/explorar/conectores", json={}, headers=tokens["admin_lgpd"]).json()["pode_revelar"] is True


# ---------- EX3: allowlist / só leitura ----------
@pytest.mark.parametrize("corpo", [
    {**BASE, "camada": "prata", "tabela": "controle.usuarios"},
    {**BASE, "camada": "prata", "tabela": "vinculos; DROP TABLE x"},
    {**BASE, "camada": "ouro", "tabela": "municipes"},
    {**BASE, "tabela": "Pacientes' OR '1'='1"},
    {**BASE, "conector": "SAUDE' --"},
])
def test_ex3_allowlist_tabela(client, tokens, dados, corpo):
    assert client.post("/api/explorar/amostra", json=corpo, headers=tokens["admin"]).status_code in (404, 422)


@pytest.mark.parametrize("col", ["senha_hash", "x) OR 1=1 --", "dados->>'cpf'"])
def test_ex3_allowlist_coluna(client, tokens, dados, col):
    for camada, tab in (("bronze", "Pacientes"), ("prata", "vinculos")):
        r = client.post("/api/explorar/amostra", json={**BASE, "camada": camada, "tabela": tab, "filtros": {col: "1"}},
                        headers=tokens["admin"])
        assert r.status_code in (403, 422)  # 403 se o nome "parece" coluna pessoal; nunca vira SQL


def test_ex3_sem_rota_de_escrita_export_ou_sql():
    from app.explorador.rotas import explorador
    rotas = [(r.path, sorted(r.methods)) for r in explorador.routes]
    assert rotas and all(m == ["POST"] for _, m in rotas)
    nomes = " ".join(p for p, _ in rotas)
    assert not any(w in nomes for w in ("csv", "export", "sql", "editar", "update", "delete"))


def test_ex3_nao_importa_conector_de_origem():
    import pathlib
    src = "".join(p.read_text() for p in pathlib.Path("app/explorador").glob("*.py"))
    assert "connectors" not in src and "pyodbc" not in src and "pymssql" not in src


# ---------- EX4 / funil ----------
def test_ex4_pendencias_prata(client, tokens, dados):
    r = client.post("/api/explorar/amostra", json={**BASE, "camada": "prata", "tabela": "vinculos", "somente_pendencias": True},
                    headers=tokens["admin"]).json()
    l0 = r["linhas"][0]
    assert r["tabela"] == "pendencias" and l0["motivo"] == "Sem correspondência" and l0["motivo_codigo"] == "sem_correspondencia"
    assert l0["chave"] == "P0***" and l0["data_carga"] and "nome" not in l0 and "quarentena_disponivel" in r
    assert "ignorad" not in str(r).lower()


def test_ex4_funil(client, tokens, dados):
    f = client.post("/api/explorar/funil", json=BASE, headers=tokens["admin"]).json()
    assert f["bronze"] == 120 and f["prata"] == 1 and f["ouro"] == 1
    assert {"etapa": "Bronze → Prata", "motivo": "sem_correspondencia", "rotulo": "Sem correspondência", "quantidade": 1} in f["descartes"]


# ---------- EX5 ----------
def test_ex5_linhagem(client, tokens, dados):
    r = client.post("/api/explorar/linhagem", json={"cidadao_id": CID}, headers=tokens["admin"])
    j = r.json()
    assert j["prata"][0]["origem"] == S and j["prata"][0]["chave"] == "P0***"
    camadas = {(v["camada"], v["tabela"]) for v in j["veio_de"]}
    assert camadas == {("prata", "vinculos"), ("bronze", "Pacientes")}
    alvo = next(v["abrir"] for v in j["veio_de"] if v["camada"] == "bronze")
    r2 = client.post("/api/explorar/amostra", json={"conector": S, **alvo}, headers=tokens["admin"]).json()
    assert [x["id_origem"] for x in r2["linhas"]] == ["P000"]
    assert j["identificadores"][0]["valor"] == "***.982.247-**"
    _sem_pii(r.text)
    assert client.post("/api/explorar/linhagem", json={"cidadao_id": "x' OR 1=1"}, headers=tokens["admin"]).status_code == 422


# ---------- unidade da máscara ----------
@pytest.mark.parametrize("col,tipo", [("cpf", "documento"), ("CPF_CNPJ", "documento"), ("cnpj", "documento"),
                                      ("cns", "cns"), ("nome", "nome"), ("nome_mae", "nome"), ("mae", "nome"),
                                      ("endereco", "endereco"), ("logradouro", "endereco"), ("telefone", "telefone"),
                                      ("celular", "telefone"), ("email", "email"), ("e_mail", "email"),
                                      ("data_nascimento", "nascimento"), ("dt_nasc", "nascimento"), ("data_obito", "obito"),
                                      ("falecido", "obito"), ("grupo", None)])
def test_tipo_sensivel(col, tipo):
    assert tipo_sensivel(col) == tipo


def test_mascara_valores():
    assert mascara_campo("cnpj", CNPJ) == "**.222.***/****-**"
    assert mascara_campo("email", EMAIL) == "f***@exemplo.test"
    assert CNS not in mascara_campo("cns", CNS)
    assert EMAIL not in mascara_campo("obs", f"contato {EMAIL}")
    assert mascara_campo("detalhe", {"cpf": CPF, "x": 1}) == {"cpf": "***.982.247-**", "x": 1}


def test_ouro_obito_e_falecido_mascarados(client, tokens, dados):
    for perfil in ("admin", "admin_lgpd"):
        r = client.post("/api/explorar/amostra", json={**BASE, "camada": "ouro", "tabela": "cidadao_dados"}, headers=tokens[perfil])
        lin = {x["campo"]: x["valor"] for x in r.json()["linhas"]}
        assert lin["data_obito"] != OBITO and lin["falecido"] != "sim" and lin["unidade"] == "UBS Central"
        _sem_pii(r.text)
    r = client.post("/api/explorar/revelar", json={**BASE, "camada": "ouro", "tabela": "cidadao_dados", "linha_id": 0, "coluna": "valor",
                                                   "motivo": MOT}, headers=tokens["admin"])
    assert r.status_code == 403


@pytest.mark.parametrize("perfil", ["admin", "operador", "leitura"])
def test_servidor_nunca_devolve_dado_em_claro(client, tokens, dados, perfil):
    """GET/POST direto na API, sem a tela: nenhuma rota devolve dado pessoal em claro (inclui exemplos do perfil)."""
    h = tokens[perfil]
    corpos = [("conectores", {}), ("funil", BASE), ("linhagem", {"cidadao_id": CID})]
    for camada, tabs in (("bronze", ["Pacientes"]), ("prata", ["cidadaos", "identificadores", "vinculos", "correlacoes_pendentes"]),
                         ("ouro", ["cidadao_dados", "cidadao_sistemas"])):
        corpos.append(("tabelas", {**BASE, "camada": camada}))
        for t in tabs:
            corpos += [("perfil", {**BASE, "camada": camada, "tabela": t}), ("amostra", {**BASE, "camada": camada, "tabela": t}),
                       ("amostra", {**BASE, "camada": camada, "tabela": t, "somente_pendencias": True})]
    for rota, corpo in corpos:
        _sem_pii(client.get(f"/api/explorar/{rota}", headers=h).text)
        r = client.post(f"/api/explorar/{rota}", json=corpo, headers=h)
        assert r.status_code == (200 if perfil == "admin" else 403), (rota, r.text)
        _sem_pii(r.text)


def test_perfil_distintos_aprox_em_amostra_limitada(client, tokens, dados, monkeypatch):
    from app.explorador import catalogo
    h = tokens["admin"]
    p = client.post("/api/explorar/perfil", json=BASE, headers=h).json()
    assert p["aprox"] is False and all(c["aprox"] is False for c in p["colunas"])
    monkeypatch.setattr(catalogo, "PERFIL_AMOSTRA", 10)
    p = client.post("/api/explorar/perfil", json=BASE, headers=h).json()
    cols = {c["coluna"]: c for c in p["colunas"]}
    assert p["aprox"] is True and p["linhas"] == 120 and p["amostra_perfil"] == 10
    assert cols["id"]["distintos"] == 10 and cols["id"]["aprox"] is True
    assert cols["grupo"]["tipo"] == "Texto" and cols["id"]["tipo"] == "Número" and cols["id"]["tipo_tecnico"] == "json:number"
    pr = client.post("/api/explorar/perfil", json={**BASE, "camada": "prata", "tabela": "vinculos"}, headers=h).json()
    assert pr["colunas"][0]["tipo"] == "Número"


def test_perfil_sem_count_distinct_na_tabela_inteira():
    import pathlib
    src = pathlib.Path("app/explorador/servico.py").read_text()
    assert "LIMIT :n" in src and ".limit(n).subquery()" in src


# ---------- integração com #14 (CC7 etapa, CC9 quarentena e reconciliação) ----------
@pytest.fixture(scope="module")
def quar(client):
    """Conector sintético EXQUAR: 10 linhas na origem = 8 na bronze + 2 em quarentena (chave nula); carga na etapa prata."""
    import json as _j

    from app.core.db import SessionLocal
    from app.models.controle import CheckpointCarga
    db = SessionLocal()
    eid = db.execute(text("INSERT INTO controle.execucoes (tipo, sistema, status, etapa, atualizado_em) VALUES "
                          "('carga_completa', 'EXQUAR', 'em_andamento', 'prata', now()) RETURNING id")).scalar()
    for i in range(10):
        q = "chave_nula" if i >= 8 else None
        db.execute(text("INSERT INTO bronze.registros (sistema_origem, tabela, id_origem, dados, hash, execucao_id, quarentena) "
                        "VALUES ('EXQUAR', 'Leilao', :io, CAST(:d AS jsonb), :h, :e, :q)"),
                   {"io": f"L{i}", "d": _j.dumps({"Quadra": None if q else f"Q{i}", "cpf": CPF}), "h": f"q{i}", "e": eid, "q": q})
    db.add(CheckpointCarga(sistema="EXQUAR", tabela="Leilao", posicao=10, total_origem=10, concluida=True, execucao_id=eid))
    db.commit()
    db.close()


def test_cc9_funil_bate_com_reconciliacao(client, tokens, quar, db):
    from app.services.carga_completa import reconciliacao
    f = client.post("/api/explorar/funil", json={"conector": "EXQUAR"}, headers=tokens["admin"]).json()
    rec = reconciliacao(db, "EXQUAR")
    assert f["bronze_ok"] == rec["Leilao"]["bronze"] == 8 and f["quarentena"] == rec["Leilao"]["quarentena"] == 2
    r = f["reconciliacao"]
    assert r["origem"] == 10 and r["bronze"] + r["quarentena"] == r["origem"] and r["ok"] is True
    assert r["status"] == "ok_com_pendencias" and f["bronze_ok"] + f["quarentena"] == r["origem"]
    assert {"etapa": "Bronze → Prata", "motivo": "em_quarentena", "rotulo": "Chave nula na origem", "quantidade": 2} in f["descartes"]
    assert f["quarentena_disponivel"] is True


def test_cc9_reconciliacao_divergente(client, tokens, quar):
    from app.core.db import SessionLocal
    s = SessionLocal()
    s.execute(text("UPDATE controle.checkpoints_carga SET total_origem = 11 WHERE sistema='EXQUAR'"))
    s.commit()
    try:
        r = client.post("/api/explorar/funil", json={"conector": "EXQUAR"}, headers=tokens["admin"]).json()["reconciliacao"]
        assert r["ok"] is False and r["status"] == "divergente"
    finally:
        s.execute(text("UPDATE controle.checkpoints_carga SET total_origem = 10 WHERE sistema='EXQUAR'"))
        s.commit()
        s.close()


def test_cc9_quarentena_nas_pendencias_e_na_bronze(client, tokens, quar):
    h = tokens["admin"]
    tabs = client.post("/api/explorar/tabelas", json={"conector": "EXQUAR", "camada": "prata"}, headers=h).json()
    assert tabs["vazia"]
    p = client.post("/api/explorar/amostra", json={"conector": "EXQUAR", "camada": "prata", "tabela": "vinculos", "somente_pendencias": True},
                    headers=h).json()
    assert [x["motivo"] for x in p["linhas"]] == ["Chave nula na origem"] * 2 and p["quarentena_disponivel"] is True
    assert all(x["motivo_codigo"] == "quarentena:chave_nula" and x["chave"].endswith("***") for x in p["linhas"])
    f = client.post("/api/explorar/amostra", json={"conector": "EXQUAR", "camada": "prata", "tabela": "vinculos", "somente_pendencias": True,
                                                    "filtros": {"motivo": "em_quarentena"}}, headers=h).json()
    assert len(f["linhas"]) == 2
    b = client.post("/api/explorar/amostra", json={"conector": "EXQUAR", "camada": "bronze", "tabela": "Leilao"}, headers=h).json()
    assert "quarentena" in b["colunas"] and [x["quarentena"] for x in b["linhas"]].count("chave_nula") == 2
    _sem_pii(str(p) + str(b))


def test_cc7_estado_etapa_x_de_3(client, tokens, quar):
    e = client.post("/api/explorar/tabelas", json={"conector": "EXQUAR", "camada": "ouro"}, headers=tokens["admin"]).json()["estado"]
    assert e["processando"] and e["etapa"] == "prata" and e["etapa_num"] == 2 and e["etapas_total"] == 3 and e["parcial"]
    assert e["mensagem"] == "Processando… etapa 2 de 3 · Tratando (prata)" and e["atualizado_em"]


# ---------- P1: bronze sem catálogo (ressalvas 1 e 3 do Tester no #16) ----------
ZZ = "ZZTESTE"


@pytest.fixture(scope="module")
def zz(client):
    """Conector sintético ZZTESTE com colunas de nome fora do padrão (NU_INSCR, Ident) carregando CPF/CNPJ/e-mail/CNS."""
    import json as _j

    from app.core.db import SessionLocal
    from app.explorador import catalogo as cat
    cat.BRONZE_COLUNAS_LIVRES[ZZ] = {"Cadastro": frozenset({"Situacao"})}
    db = SessionLocal()
    eid = db.execute(text("INSERT INTO controle.execucoes (tipo, sistema, status, finalizado_em) VALUES "
                          "('carga_demo_amostra', :s, 'sucesso', now()) RETURNING id"), {"s": ZZ}).scalar()
    cid = str(uuid.uuid5(uuid.NAMESPACE_DNS, "zzteste-1"))
    ids = []
    for i in range(3):
        d = {"NU_INSCR": CPF, "Ident": CNPJ, "Contato": EMAIL, "Cartao": CNS, "Situacao": "ATIVO", "Cod_Interno": 700 + i}
        ids.append(db.execute(text("INSERT INTO bronze.registros (sistema_origem, tabela, id_origem, dados, hash, execucao_id) "
                                   "VALUES (:s, 'Cadastro', :io, CAST(:d AS jsonb), :h, :e) RETURNING id"),
                              {"s": ZZ, "io": f"Z{i}", "d": _j.dumps(d), "h": f"zz{i}", "e": eid}).scalar())
    db.execute(text("INSERT INTO silver.cidadaos (cidadao_id, nome) VALUES (:c, 'ZZTESTE Sintetico')"), {"c": cid})
    db.execute(text("INSERT INTO silver.vinculos (cidadao_id, sistema, tabela, chave_origem, bronze_id, metodo, confianca, atributos) "
                    "VALUES (:c, :s, 'Cadastro', 'Z0', :b, 'cpf', 1.0, '[]')"), {"c": cid, "s": ZZ, "b": ids[0]})
    db.commit()
    db.close()
    yield {"ids": ids, "cid": cid}
    cat.BRONZE_COLUNAS_LIVRES.pop(ZZ, None)


ZB = {"conector": ZZ, "camada": "bronze", "tabela": "Cadastro"}
ZPII = (CPF, CNPJ, EMAIL, CNS, "fulano.sintetico", "700", "701")


def _zsem(txt):
    for p in ZPII:
        assert p not in txt, f"vazou {p[:4]}…"


@pytest.mark.parametrize("col", ["NU_INSCR", "Ident", "Contato", "Cartao", "Cod_Interno", "Situacao", "id_origem"])
def test_p1_bronze_filtro_alem_de_id_admin_comum_403_auditado(client, tokens, zz, db, col):
    antes = db.execute(text("SELECT count(*) FROM controle.auditoria WHERE acao='explorar_filtro_pessoal_negado'")).scalar()
    r = client.post("/api/explorar/amostra", json={**ZB, "filtros": {col: "1"}, "motivo_filtro": MOT}, headers=tokens["admin"])
    assert r.status_code == 403
    db.expire_all()
    n = db.execute(text("SELECT count(*) FROM controle.auditoria WHERE acao='explorar_filtro_pessoal_negado'")).scalar()
    det = db.execute(text("SELECT detalhe::text FROM controle.auditoria WHERE acao='explorar_filtro_pessoal_negado' "
                          "ORDER BY id DESC LIMIT 1")).scalar()
    assert n == antes + 1 and col in det and "1" not in det.replace(col, "")
    # somente_pendencias na bronze não libera o filtro
    r = client.post("/api/explorar/amostra", json={**ZB, "filtros": {col: "1"}, "somente_pendencias": True}, headers=tokens["admin"])
    assert r.status_code == 403


def test_p1_bronze_filtro_lgpd_exige_motivo_e_audita_sem_valor(client, tokens, zz, db):
    lg = tokens["admin_lgpd"]
    assert client.post("/api/explorar/amostra", json={**ZB, "filtros": {"NU_INSCR": CPF}}, headers=lg).status_code == 400
    r = client.post("/api/explorar/amostra", json={**ZB, "filtros": {"NU_INSCR": CPF}, "motivo_filtro": MOT}, headers=lg)
    assert r.status_code == 200 and len(r.json()["linhas"]) == 3
    _zsem(r.text)
    db.expire_all()
    det = db.execute(text("SELECT detalhe::text FROM controle.auditoria WHERE acao='explorar_filtro_pessoal' ORDER BY id DESC LIMIT 1")).scalar()
    assert "NU_INSCR" in det and "chamado 123" in det and CPF not in det
    r = client.post("/api/explorar/amostra", json={**ZB, "filtros": {"id": str(zz["ids"][0])}}, headers=tokens["admin"])
    assert r.status_code == 200 and len(r.json()["linhas"]) == 1


@pytest.mark.parametrize("perfil", ["admin", "admin_lgpd"])
def test_p1_bronze_colunas_fora_da_allowlist_mascaradas(client, tokens, zz, perfil):
    r = client.post("/api/explorar/amostra", json=ZB, headers=tokens[perfil])
    j = r.json()
    l0 = j["linhas"][0]
    assert l0["Situacao"] == "ATIVO"  # na allowlist do conector: em claro
    assert l0["NU_INSCR"] == l0["Ident"] == l0["Contato"] == l0["Cartao"] == l0["Cod_Interno"] == "••• (oculto)"
    assert set(j["colunas_sensiveis"]) == {"NU_INSCR", "Ident", "Contato", "Cartao", "Cod_Interno"}
    _zsem(r.text)
    p = client.post("/api/explorar/perfil", json=ZB, headers=tokens[perfil])
    sens = {c["coluna"]: c["sensivel"] for c in p.json()["colunas"]}
    assert sens["NU_INSCR"] and sens["Ident"] and not sens["Situacao"]
    _zsem(p.text)


def test_p1_bronze_revelar_coluna_fora_do_padrao_so_lgpd(client, tokens, zz):
    corpo = {**ZB, "linha_id": zz["ids"][0], "coluna": "NU_INSCR", "motivo": MOT}
    assert client.post("/api/explorar/revelar", json=corpo, headers=tokens["admin"]).status_code == 403
    r = client.post("/api/explorar/revelar", json=corpo, headers=tokens["admin_lgpd"])
    assert r.status_code == 200 and r.json()["valor"] == CPF


def test_p1_linhagem_admin_comum_mascarada(client, tokens, zz):
    r = client.post("/api/explorar/linhagem", json={"cidadao_id": zz["cid"]}, headers=tokens["admin"])
    assert r.status_code == 200
    b = r.json()["prata"][0]["bronze"]["dados"]
    assert b["Situacao"] == "ATIVO" and b["NU_INSCR"] == "••• (oculto)" and b["Contato"] == "••• (oculto)"
    _zsem(r.text)


def test_p1_allowlist_bronze_e_subconjunto_nao_pessoal_da_politica():
    from app.connectors.sqlserver.politicas import POLITICAS, coluna_proibida
    from app.explorador.catalogo import BRONZE_COLUNAS_LIVRES
    for sist, tabs in BRONZE_COLUNAS_LIVRES.items():
        if sist == ZZ:
            continue
        pol = POLITICAS[sist]
        for tab, cols in tabs.items():
            assert cols <= set(pol.colunas[tab]), (sist, tab)
            assert not any(tipo_sensivel(c) or coluna_proibida(c, pol.proibidas) for c in cols), (sist, tab)


@pytest.fixture(scope="module")
def saude_ids(client):
    """SAUDE sintético (ZZTESTE): Matricula, UUID e Codigo_Cadastro ficam fora da allowlist (decisão do TL)."""
    import json as _j

    from app.core.db import SessionLocal
    db = SessionLocal()
    eid = db.execute(text("INSERT INTO controle.execucoes (tipo, sistema, status, finalizado_em) VALUES "
                          "('carga_demo_amostra', 'SAUDE', 'sucesso', now()) RETURNING id")).scalar()
    ins = ("INSERT INTO bronze.registros (sistema_origem, tabela, id_origem, dados, hash, execucao_id) "
           "VALUES ('SAUDE', :t, :io, CAST(:d AS jsonb), :h, :e) RETURNING id")
    pid = db.execute(text(ins), {"t": "pacientes", "io": "ZZM1", "e": eid, "h": "zzm1",
                                 "d": _j.dumps({"Matricula": "ZZT777001", "Sexo": "F"})}).scalar()
    did = db.execute(text(ins), {"t": "ESUS_Cadastro_Domiciliar", "io": "ZZD1", "e": eid, "h": "zzd1",
                                 "d": _j.dumps({"Codigo_Cadastro": "ZZT888002", "UUID": "ZZT-UUID-0003", "Nu_INE": "42"})}).scalar()
    cid = str(uuid.uuid5(uuid.NAMESPACE_DNS, "zzteste-saude"))
    db.execute(text("INSERT INTO silver.cidadaos (cidadao_id, nome) VALUES (:c, 'ZZTESTE Saude')"), {"c": cid})
    db.execute(text("INSERT INTO silver.vinculos (cidadao_id, sistema, tabela, chave_origem, bronze_id, metodo, confianca, atributos) "
                    "VALUES (:c, 'SAUDE', 'pacientes', 'ZZM1', :b, 'cpf', 1.0, '[]')"), {"c": cid, "b": pid})
    db.commit()
    db.close()
    return {"pid": pid, "did": did, "cid": cid}


def test_tl_matricula_uuid_codigo_cadastro_mascarados_admin_e_lgpd_revela(client, tokens, saude_ids):
    vals = ("ZZT777001", "ZZT888002", "ZZT-UUID-0003")
    h = tokens["admin"]
    a = client.post("/api/explorar/amostra", json={"conector": "SAUDE", "camada": "bronze", "tabela": "pacientes",
                                                    "filtros": {"id": str(saude_ids["pid"])}}, headers=h)
    l0 = a.json()["linhas"][0]
    assert l0["Matricula"] == "••• (oculto)" and l0["Sexo"] == "F" and "Matricula" in a.json()["colunas_sensiveis"]
    d = client.post("/api/explorar/amostra", json={"conector": "SAUDE", "camada": "bronze", "tabela": "ESUS_Cadastro_Domiciliar",
                                                    "filtros": {"id": str(saude_ids["did"])}}, headers=h).json()["linhas"][0]
    assert d["Codigo_Cadastro"] == d["UUID"] == "••• (oculto)" and d["Nu_INE"] == "42"
    lin = client.post("/api/explorar/linhagem", json={"cidadao_id": saude_ids["cid"]}, headers=h)
    assert lin.json()["prata"][0]["bronze"]["dados"]["Matricula"] == "••• (oculto)"
    for t in (a.text, lin.text):
        assert not any(v in t for v in vals)
    lg = tokens["admin_lgpd"]
    for tab, lid, col, val in (("pacientes", saude_ids["pid"], "Matricula", vals[0]),
                               ("ESUS_Cadastro_Domiciliar", saude_ids["did"], "Codigo_Cadastro", vals[1]),
                               ("ESUS_Cadastro_Domiciliar", saude_ids["did"], "UUID", vals[2])):
        corpo = {"conector": "SAUDE", "camada": "bronze", "tabela": tab, "linha_id": lid, "coluna": col, "motivo": MOT}
        assert client.post("/api/explorar/revelar", json=corpo, headers=h).status_code == 403
        r = client.post("/api/explorar/revelar", json=corpo, headers=lg)
        assert r.status_code == 200 and r.json()["valor"] == val
