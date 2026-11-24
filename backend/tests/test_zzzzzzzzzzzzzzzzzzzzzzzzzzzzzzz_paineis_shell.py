"""Shell de painéis (onda 0): P01–P08, S02, K01–K04. Banco de teste (_test), dados ZZTESTE sintéticos, contas @aion.local."""
import re
import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import event, text

from app.core.db import get_engine
from app.core.security import hash_senha
from app.gold import cubos_consulta
from app.models.controle import Usuario, UsuarioSecretaria
from app.paineis import exemplo, privacidade, servico
from tests.conftest import SENHA

URL = "/api/paineis/folha-modelo"
HOJE = date.today()


def _usuario(db, client, perfil="leitura", secretarias=()):
    email = f"zzteste-painel-{uuid.uuid4().hex[:8]}@aion.local"
    u = Usuario(email=email, nome="ZZTESTE painel", perfil=perfil, senha_hash=hash_senha(SENHA))
    db.add(u)
    db.commit()
    for s in secretarias:
        db.add(UsuarioSecretaria(usuario_id=u.id, secretaria=s, vigencia_inicio=HOJE - timedelta(days=10), cadastrado_por="admin@aion.local"))
    db.commit()
    r = client.post("/api/auth/login", json={"usuario": email, "senha": SENHA})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture(scope="module")
def dados(client):
    from app.core.db import SessionLocal
    s = SessionLocal()
    r = exemplo.gerar(s)
    s.close()
    return r


@pytest.fixture
def prefeito(db, client):
    return _usuario(db, client, secretarias=("TODAS",))


# ---------- estados (P04) ----------
def test_sem_fonte_quando_nao_ha_execucao(client, tokens, monkeypatch):
    p = servico.Painel(**{**servico.FOLHA_MODELO.__dict__, "chave": "zz-sem-fonte", "sistema": "ZZTESTE_SEM_EXECUCAO"})
    monkeypatch.setitem(servico.PAINEIS, p.chave, p)
    r = client.get("/api/paineis/zz-sem-fonte", headers=tokens["admin"])
    assert r.status_code == 200 and r.json()["estado"] == "sem_fonte" and r.json()["fonte"] is None


def test_vazio_quando_ha_execucao_sem_fato(client, tokens, dados, monkeypatch):
    p = servico.Painel(**{**servico.FOLHA_MODELO.__dict__, "chave": "zz-vazio"})
    monkeypatch.setitem(servico.PAINEIS, p.chave, p)
    r = client.get("/api/paineis/zz-vazio", headers=tokens["admin"]).json()
    assert r["estado"] == "vazio" and r["fonte"]["execucao_id"] == dados["execucao_id"]


def test_competencia_sem_carga_e_vazio_com_link_para_ultima(client, tokens, dados):
    r = client.get(URL, params={"competencia": exemplo.MES_SEM_CARGA}, headers=tokens["admin"]).json()
    assert r["estado"] == "vazio" and r["ultima_competencia"]["valor"] == exemplo.FIM


def test_competencia_invalida_400(client, tokens, dados):
    assert client.get(URL, params={"competencia": "2026-13"}, headers=tokens["admin"]).status_code == 400


def test_gerador_recusa_banco_sem_test():
    class Falso:
        def scalar(self, *_a, **_k):
            return "sigpmf"
    with pytest.raises(exemplo.BancoNaoTeste):
        exemplo.gerar(Falso())


# ---------- P01/P02/P03 ----------
def test_kpis_variacoes_sparkline_e_fonte(client, tokens, dados, db):
    r = client.get(URL, headers=tokens["admin"]).json()
    assert r["estado"] == "ok" and r["exemplo"] is True and r["filtros"]["competencia"] == exemplo.FIM
    assert r["fonte"]["execucao_id"] == dados["execucao_id"]
    em = db.scalar(text("SELECT finalizado_em FROM controle.execucoes WHERE id = :i"), {"i": dados["execucao_id"]})
    assert r["fonte"]["carga_em"] == em.isoformat()  # vem da tabela de execuções, não fixa
    assert [k["chave"] for k in r["kpis"]] == ["folha_bruta", "horas_extras", "pct_he", "servidores", "custo_medio"]
    m1, m2 = (float(x) for x in db.execute(text("SELECT sum(m1), sum(m2) FROM gold.painel_fato WHERE painel='folha-modelo' AND competencia=:c"),
                                          {"c": exemplo.FIM}).one())
    pct = next(k for k in r["kpis"] if k["chave"] == "pct_he")
    assert pct["valor"] == pytest.approx(100 * m2 / m1, abs=0.01)
    for k in r["kpis"]:
        assert len(k["serie"]) == 12 and k["serie"][-1]["competencia"] == exemplo.FIM
        assert k["mom"]["delta"] is not None and k["yoy"]["delta"] is not None  # 13+ competências
        assert k["mom"]["sentido"] in ("alta", "queda", "estavel")
        lac = next(p for p in k["serie"] if p["competencia"] == exemplo.MES_SEM_CARGA)
        assert lac["valor"] is None and lac["sem_carga"] is True  # lacuna, nunca 0


def test_variacao_que_depende_do_mes_sem_carga_e_traco(client, tokens, dados):
    r = client.get(URL, params={"competencia": "2026-07"}, headers=tokens["admin"]).json()
    for k in r["kpis"]:
        assert k["mom"]["delta"] is None and k["mom"]["referencia"] == "jun/2026"
        assert k["yoy"]["delta"] is not None


# ---------- P05/K04 drill-down fecha com o total ----------
def _confere_soma(d):
    assert d["total"] is not None
    for m in ("m1", "m2", "n"):
        assert sum(i[m] for i in d["itens"]) == pytest.approx(d["total"][m])


def test_drill_fecha_em_todos_os_niveis(client, tokens, dados):
    r = client.get(URL, headers=tokens["admin"]).json()
    d0 = r["drill"]
    assert d0["indice"] == 0 and [m["rotulo"] for m in d0["migalhas"]] == ["Prefeitura"]
    _confere_soma(d0)
    tot_pref = d0["total"]
    k_pct = next(k for k in r["kpis"] if k["chave"] == "pct_he")
    assert d0["total"]["valor"] == pytest.approx(k_pct["valor"], abs=0.01)
    r1 = client.get(URL, params={"secretaria": "SAUDE"}, headers=tokens["admin"]).json()["drill"]
    _confere_soma(r1)
    saude0 = next(i for i in d0["itens"] if i["chave"] == "SAUDE")
    assert r1["total"]["m1"] == pytest.approx(saude0["m1"]) and r1["total"]["n"] == saude0["n"]
    assert any(i["outros"] for i in r1["itens"])  # Vigilância (n<5) foi para Outros e entrou na soma
    org = next(i for i in r1["itens"] if i["abre"])
    r2 = client.get(URL, params={"secretaria": "SAUDE", "caminho": [org["chave"]]}, headers=tokens["admin"]).json()["drill"]
    _confere_soma(r2)
    assert [m["rotulo"] for m in r2["migalhas"]][-1] == org["chave"] and all(not i["abre"] for i in r2["itens"])
    assert tot_pref["n"] == sum(i["n"] for i in d0["itens"])


def test_nenhum_grupo_visivel_abaixo_de_k(client, tokens, dados):
    for sec in (None, "SAUDE", "ADMINISTRACAO", "FAZENDA"):
        d = client.get(URL, params={"secretaria": sec} if sec else {}, headers=tokens["admin"]).json()["drill"]
        assert all(i["n"] >= 5 for i in d["itens"] if not i["outros"])
        if d["itens"]:
            outros = [i for i in d["itens"] if i["outros"]]
            assert not outros or outros[0]["n"] >= 5 or len(d["itens"]) == 1


def test_recorte_pequeno_nao_mostra_valor(client, tokens, dados):
    r = client.get(URL, params={"secretaria": "ADMINISTRACAO"}, headers=tokens["admin"]).json()  # 4 servidores
    assert all(k["valor"] is None and k["suprimido"] for k in r["kpis"])
    assert r["drill"]["suprimido"] and r["drill"]["itens"] == [] and r["drill"]["total"] is None


# ---------- K01/K02: ponto único, teste por cubo ----------
@pytest.mark.parametrize("cubo", sorted(privacidade.K_POR_CUBO))
def test_k_por_cubo(cubo):
    k = privacidade.k_do_cubo(cubo)
    assert k == (10 if cubo == "saude_rara" else 5)
    itens = [{"rotulo": f"ZZTESTE {i}", "n": n, "m1": n * 10.0, "m2": n * 1.0} for i, n in enumerate((1, k - 1, k, k + 3, 2 * k))]
    vis, ocultos = privacidade.suprime(itens, k)
    assert ocultos >= 2
    assert all(i["n"] >= k for i in vis)  # inclusive "Outros"
    for m in ("n", "m1", "m2"):
        assert sum(i[m] for i in vis) == pytest.approx(sum(i[m] for i in itens))


def test_cubos_do_ouro_tem_k_no_ponto_unico():
    for cubo in cubos_consulta.CUBOS:
        assert cubo in privacidade.K_POR_CUBO
    import inspect
    assert "k_do_cubo(cubo)" in inspect.getsource(cubos_consulta.consulta)


def test_outros_complementar_nao_fica_abaixo_de_k():
    vis, _ = privacidade.suprime([{"rotulo": "a", "n": 2, "m1": 1, "m2": 1}, {"rotulo": "b", "n": 6, "m1": 1, "m2": 1},
                                  {"rotulo": "c", "n": 9, "m1": 1, "m2": 1}], 5)
    assert [i["rotulo"] for i in vis] == ["c", privacidade.OUTROS] and vis[-1]["n"] == 8


# ---------- P08 escopo no backend ----------
def test_sem_vinculo_403(client, db, dados):
    h = _usuario(db, client)
    assert client.get(URL, headers=h).status_code == 403


def test_secretario_so_ve_a_sua(client, db, dados):
    h = _usuario(db, client, secretarias=("SAUDE",))
    r = client.get(URL, headers=h).json()
    assert r["filtros"]["secretaria"] == "SAUDE" and r["secretarias"] == ["SAUDE"]
    assert client.get(URL, params={"secretaria": "EDUCACAO"}, headers=h).status_code == 403
    total_saude = client.get(URL, params={"secretaria": "SAUDE"}, headers=h).json()["drill"]["total"]["m1"]
    assert r["drill"]["total"]["m1"] == pytest.approx(total_saude)


def test_prefeito_ve_tudo(client, prefeito, dados):
    r = client.get(URL, headers=prefeito).json()
    assert set(r["secretarias"]) == {s for s, _ in exemplo.ESTRUTURA}


# ---------- K03: nenhuma PII nas respostas ----------
CPF = re.compile(r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b")
CNPJ = re.compile(r"\b\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}\b")
UUID = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)
CHAVES_PROIBIDAS = re.compile(r'"(cpf|cnpj|ccm|nome|nome_social|cidadao_id|cidadao|cns|nis|email|matricula)"', re.I)


def _strings(o):
    if isinstance(o, dict):
        for v in o.values():
            yield from _strings(v)
    elif isinstance(o, list):
        for v in o:
            yield from _strings(v)
    elif isinstance(o, str):
        yield o


def test_respostas_sem_pii(client, tokens, dados):
    for prm in ({}, {"secretaria": "SAUDE"}, {"secretaria": "SAUDE", "caminho": ["ZZTESTE UBS Centro"]}, {"competencia": "2026-07"}):
        r = client.get(URL, params=prm, headers=tokens["admin"])
        assert r.status_code == 200
        assert not CHAVES_PROIBIDAS.search(r.text), r.text[:300]
        for s in _strings(r.json()):
            assert not CPF.search(s) and not CNPJ.search(s) and not UUID.search(s), s
            assert "@" not in s


def test_regex_pega_pii_plantada():
    assert CPF.search("000.000.001-91") and CNPJ.search("11.222.333/0001-81") and CHAVES_PROIBIDAS.search('{"cpf": "x"}')


# ---------- S02: só ouro na requisição ----------
def test_endpoint_nao_le_prata_nem_bronze(client, tokens, dados):
    vistos: list[str] = []

    def ouvir(conn, cursor, statement, *_a):
        vistos.append(statement.lower())
    eng = get_engine()
    event.listen(eng, "before_cursor_execute", ouvir)
    try:
        for prm in ({}, {"secretaria": "SAUDE"}, {"secretaria": "SAUDE", "caminho": ["ZZTESTE UBS Centro"]}):
            assert client.get(URL, params=prm, headers=tokens["admin"]).status_code == 200
    finally:
        event.remove(eng, "before_cursor_execute", ouvir)
    painel_sql = [s for s in vistos if "painel_fato" in s or "execucoes" in s]
    assert painel_sql
    assert not any(re.search(r"\b(silver|bronze)\.", s) for s in vistos)


def test_migracao_aditiva_existe(db):
    assert db.scalar(text("SELECT to_regclass('gold.painel_fato')")) is not None
