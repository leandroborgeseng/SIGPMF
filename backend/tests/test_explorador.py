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
PII = (CPF, CNPJ, CNS, NOME, MAE, END, TEL, EMAIL, "Fulano", "Beltrana", "Ficticia", "999887766", "fulano.sintetico")
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
             "nome": NOME if i == 0 else f"Pessoa {i:03d} Exemplo", "nome_mae": MAE, "endereco": END, "telefone": TEL,
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
                    "(:c, 'saude', 'cpf', :v, :s, true), (:c, 'saude', 'unidade', 'UBS Central', :s, true)"), {"c": CID, "v": CPF, "s": S})
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
    r = client.post("/api/explorar/amostra", json={**BASE, "filtros": {"cpf": CPF, "nome": "Fulano"}}, headers=tokens["admin"])
    assert r.status_code == 200
    db.expire_all()
    det = db.execute(text("SELECT detalhe::text FROM controle.auditoria WHERE acao='explorar_amostra' ORDER BY id DESC LIMIT 1")).scalar()
    assert "cpf" in det and "nome" in det
    _sem_pii(det)


def test_ex6_sem_pii_em_log_nem_url(client, tokens, dados, caplog):
    caplog.set_level(logging.DEBUG)
    urls = []
    for rota, corpo in (("conectores", {}), ("tabelas", BASE), ("perfil", BASE), ("funil", BASE),
                        ("amostra", {**BASE, "filtros": {"cpf": CPF, "email": EMAIL}}), ("linhagem", {"cidadao_id": CID})):
        r = client.post(f"/api/explorar/{rota}", json=corpo, headers=tokens["admin"])
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
        assert len(r["linhas"]) == n and r["tem_proxima"] is prox and r["tem_anterior"] is (pag > 1) and r["total"] == 120
        vistos += [x["id_origem"] for x in r["linhas"]]
    assert len(set(vistos)) == 120


def test_ex2_mascara_todas_colunas_pessoais(client, tokens, dados):
    r = client.post("/api/explorar/amostra", json=BASE, headers=tokens["admin"]).json()
    l0, l1 = r["linhas"][0], r["linhas"][1]
    assert l0["cpf"] == "***.982.247-**" and l0["nome"] == "F*** S*** T***" and l0["nome_mae"] == "B*** S*** T***"
    assert l0["email"] == "f***@exemplo.test" and l0["telefone"].endswith("66") and "999" not in l0["telefone"]
    assert l0["endereco"] != END and l0["cns"] != CNS and l0["cnpj"] != CNPJ
    assert CPF not in l1["obs"]  # CPF em texto livre também é mascarado
    _sem_pii(str(r))
    for camada, tab in (("prata", "identificadores"), ("prata", "cidadaos"), ("ouro", "cidadao_dados")):
        x = client.post("/api/explorar/amostra", json={**BASE, "camada": camada, "tabela": tab}, headers=tokens["admin_lgpd"])
        assert x.status_code == 200 and x.json()["linhas"]
        _sem_pii(x.text)


def test_ex2_filtro_por_post(client, tokens, dados):
    h = tokens["admin"]
    r = client.post("/api/explorar/amostra", json={**BASE, "filtros": {"grupo": "a"}}, headers=h).json()
    assert r["total"] == 60
    r = client.post("/api/explorar/amostra", json={**BASE, "filtros": {"cpf": CPF}}, headers=h).json()
    assert r["total"] == 1 and r["linhas"][0]["id_origem"] == "P000"
    # coluna sensível: só igualdade exata (sem oráculo de substring)
    assert client.post("/api/explorar/amostra", json={**BASE, "filtros": {"cpf": CPF[:5]}}, headers=h).json()["total"] == 0
    r = client.post("/api/explorar/amostra", json={**BASE, "filtros": {"grupo": "%"}}, headers=h).json()
    assert r["total"] == 0  # curinga escapado


def test_ex2_revelar_so_admin_lgpd_com_motivo(client, tokens, dados, db):
    corpo = {**BASE, "linha_id": dados[0], "coluna": "cpf", "motivo": "Conferência LGPD chamado 123"}
    assert client.post("/api/explorar/revelar", json=corpo, headers=tokens["admin"]).status_code == 403
    assert client.post("/api/explorar/revelar", json={**corpo, "motivo": "curto"}, headers=tokens["admin_lgpd"]).status_code == 400
    r = client.post("/api/explorar/revelar", json=corpo, headers=tokens["admin_lgpd"])
    assert r.status_code == 200 and r.json() == {"valor": CPF, "remascarar_em_segundos": 60}
    db.expire_all()
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
        assert r.status_code == 422


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
    assert r["tabela"] == "pendencias" and r["linhas"][0]["motivo"] == "sem_correspondencia" and r["linhas"][0]["chave"] == "P005"
    assert r["linhas"][0]["nome"] != NOME and "quarentena_disponivel" in r


def test_ex4_funil(client, tokens, dados):
    f = client.post("/api/explorar/funil", json=BASE, headers=tokens["admin"]).json()
    assert f["bronze"] == 120 and f["prata"] == 1 and f["ouro"] == 1
    assert {"etapa": "bronze→prata", "motivo": "sem_correspondencia", "quantidade": 1} in f["descartes"]


# ---------- EX5 ----------
def test_ex5_linhagem(client, tokens, dados):
    r = client.post("/api/explorar/linhagem", json={"cidadao_id": CID}, headers=tokens["admin"])
    j = r.json()
    assert j["prata"][0]["origem"] == S and j["prata"][0]["chave"] == "P000" and j["prata"][0]["bronze"]["chave"] == "P000"
    assert j["identificadores"][0]["valor"] == "***.982.247-**"
    _sem_pii(r.text)
    assert client.post("/api/explorar/linhagem", json={"cidadao_id": "x' OR 1=1"}, headers=tokens["admin"]).status_code == 422


# ---------- unidade da máscara ----------
@pytest.mark.parametrize("col,tipo", [("cpf", "documento"), ("CPF_CNPJ", "documento"), ("cnpj", "documento"),
                                      ("cns", "cns"), ("nome", "nome"), ("nome_mae", "nome"), ("mae", "nome"),
                                      ("endereco", "endereco"), ("logradouro", "endereco"), ("telefone", "telefone"),
                                      ("celular", "telefone"), ("email", "email"), ("e_mail", "email"), ("grupo", None)])
def test_tipo_sensivel(col, tipo):
    assert tipo_sensivel(col) == tipo


def test_mascara_valores():
    assert mascara_campo("cnpj", CNPJ) == "**.222.***/****-**"
    assert mascara_campo("email", EMAIL) == "f***@exemplo.test"
    assert CNS not in mascara_campo("cns", CNS)
    assert EMAIL not in mascara_campo("obs", f"contato {EMAIL}")
    assert mascara_campo("detalhe", {"cpf": CPF, "x": 1}) == {"cpf": "***.982.247-**", "x": 1}
