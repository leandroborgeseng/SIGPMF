import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from tests.conftest import SENHA, TEST_URL


def _ultima_auditoria(acao):
    eng = create_engine(TEST_URL)
    with eng.connect() as c:
        return c.execute(text("select usuario, acao, alvo, criado_em, detalhe from controle.auditoria "
                              "where acao=:a order by id desc limit 1"), {"a": acao}).first()


def test_login_errado_401_auditado(client):
    r = client.post("/api/auth/login", json={"usuario": "admin@aion.local", "senha": "errada"})
    assert r.status_code == 401 and r.json()["detail"] == "Usuário ou senha inválidos"
    a = _ultima_auditoria("login_falha")
    assert a.usuario == "admin@aion.local" and a.criado_em.tzinfo is not None


def test_login_perfis(client, tokens):
    for perfil, h in tokens.items():
        assert client.get("/api/auth/me", headers=h).json()["perfil"] == perfil
    assert _ultima_auditoria("login") is not None


def test_senhas_com_hash(banco, client):
    eng = create_engine(TEST_URL)
    with eng.connect() as c:
        hashes = [h for (h,) in c.execute(text("select senha_hash from controle.usuarios"))]
    assert hashes and all(h.startswith("$2") and SENHA not in h for h in hashes)


@pytest.mark.parametrize("perfil", ["leitura", "operador", "admin_lgpd"])
def test_catalogo_so_admin(client, tokens, perfil):
    r = client.post("/api/catalogo/sistemas", json={"codigo": "X", "nome": "X"}, headers=tokens[perfil])
    assert r.status_code == 403


def test_leitura_nao_executa_carga(client, tokens):
    assert client.post("/api/conectores/demo/executar", headers=tokens["leitura"]).status_code == 403


def test_operador_nao_gerencia_usuarios(client, tokens):
    r = client.post("/api/usuarios", json={"email": "x@aion.local", "nome": "x", "perfil": "leitura", "senha": "0123456789ab"},
                    headers=tokens["operador"])
    assert r.status_code == 403


def test_catalogo_admin_cria_e_audita(client, tokens):
    h = tokens["admin"]
    r = client.post("/api/catalogo/sistemas", json={"codigo": "DEMO", "nome": "Sistema demo"}, headers=h)
    assert r.status_code == 201
    sid = r.json()["id"]
    r = client.post(f"/api/catalogo/sistemas/{sid}/tabelas", json={"nome": "pessoas", "chave": "id",
                                                                   "campo_incremental": "atualizado_em"}, headers=h)
    assert r.status_code == 201
    lista = client.get("/api/catalogo/sistemas", headers=tokens["leitura"]).json()
    demo = next(s for s in lista if s["codigo"] == "DEMO")
    assert demo["tabelas"][0]["chave"] == "id"
    assert _ultima_auditoria("alterar_catalogo").alvo == "tabela:DEMO.pessoas"
    assert client.post("/api/catalogo/sistemas", json={"codigo": "DEMO", "nome": "dup"}, headers=h).status_code == 422
    assert client.post(f"/api/catalogo/sistemas/{sid}/tabelas", json={"nome": "sem_chave"}, headers=h).status_code == 422


def test_rotas_de_dados_exigem_token(client):
    from app.main import app
    for rota in app.routes:
        path = getattr(rota, "path", "")
        if not path.startswith("/api/") or path.endswith("/health") or path == "/api/auth/login":
            continue
        url = path.replace("{cid}", "x").replace("{sid}", "1").replace("{eid}", "1").replace("{conector}", "x")
        for m in rota.methods - {"HEAD", "OPTIONS"}:
            assert client.request(m, url).status_code == 401, (m, url)
            assert client.request(m, url, headers={"Authorization": "Bearer adulterado"}).status_code == 401


def test_auditoria_sem_rota_de_alteracao():
    from app.main import app
    for rota in app.routes:
        if "auditoria" in getattr(rota, "path", ""):
            assert rota.methods <= {"GET", "HEAD"}


def test_auditoria_imutavel_no_banco(client):
    eng = create_engine(TEST_URL)
    for sql in ("update controle.auditoria set acao='x'", "delete from controle.auditoria",
                "truncate controle.auditoria"):
        with pytest.raises(DBAPIError):
            with eng.begin() as c:
                c.execute(text(sql))
