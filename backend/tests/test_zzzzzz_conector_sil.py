# Roda por último (zz): grava bronze SIL sintética que mudaria as contagens dos testes de demo.
"""Conector SIL (SIL1-SIL3, SIL5-SIL8, SIL11-SIL13) contra um servidor HTTP SINTÉTICO local (tests/sil_mock.py).
Nenhuma chamada à SIL real; nenhuma credencial real (só valores ZZTESTE inventados)."""
import logging
import pathlib
import re

import httpx
import pytest

from app.connectors.sil import carga, prata
from app.connectors.sil.cliente import (
    ClienteSil,
    FalhaCredencial,
    FalhaSil,
    OperacaoProibida,
    SilDesabilitado,
    checa_operacao,
)
from app.connectors.sil.config import ConfigSil
from app.connectors.sil.endpoints import ENDPOINTS, EndpointSil, endpoint, registrar
from tests.sil_mock import ID_SINTETICO, SEGREDO_SINTETICO, TOKEN, EstadoMock, _cpf, servidor

PACOTE = pathlib.Path(__file__).resolve().parents[1] / "app" / "connectors" / "sil"


class Relogio:
    def __init__(self):
        self.t, self.sonos = 1000.0, []

    def __call__(self):
        return self.t

    def dormir(self, s):
        self.sonos.append(s)
        self.t += s


@pytest.fixture
def mock():
    estado = EstadoMock()
    srv = servidor(estado)
    yield estado, f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def cfg(url, **kw):
    base = {"base_url": url, "client_id": ID_SINTETICO, "client_secret": SEGREDO_SINTETICO, "scope": "zzteste",
            "timeout_s": 2.0, "tentativas": 2, "backoff_s": 0.01, "req_por_s": 1000.0}
    base.update(kw)
    return ConfigSil(**base)


@pytest.fixture
def cliente(mock):
    estado, url = mock
    rel = Relogio()
    c = ClienteSil(cfg(url), relogio=rel, dormir=rel.dormir)
    yield c, estado, rel
    c.fechar()


# ---------- SIL1: desligado sem env, nada de segredo em repr/status ----------
def test_desligado_sem_variaveis(monkeypatch):
    for v in ("SIL_BASE_URL", "SIL_BASE_URL_SANDBOX", "SIL_CLIENT_ID", "SIL_CLIENT_SECRET", "SIL_SCOPE", "SIL_PASSWORD"):
        monkeypatch.delenv(v, raising=False)
    c = ConfigSil.do_ambiente()
    assert c.habilitado is False and c.resumo_publico()["habilitado"] is False
    with pytest.raises(SilDesabilitado):
        ClienteSil(c)
    monkeypatch.setenv("SIL_BASE_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("SIL_CLIENT_ID", ID_SINTETICO)
    assert ConfigSil.do_ambiente().habilitado is False  # sem segredo continua desligado
    monkeypatch.setenv("SIL_CLIENT_SECRET", SEGREDO_SINTETICO)
    monkeypatch.setenv("SIL_PASSWORD", "zzteste-senha")
    c = ConfigSil.do_ambiente()
    assert c.habilitado is True
    for txt in (repr(c), str(c), repr(ClienteSil(c)), str(c.resumo_publico())):
        assert SEGREDO_SINTETICO not in txt and ID_SINTETICO not in txt and "zzteste-senha" not in txt
        assert "127.0.0.1" not in txt


# ---------- SIL13: allowlist antes da rede ----------
@pytest.mark.parametrize("metodo,caminho", [
    ("POST", "/franca/imoveis/atualizar"), ("GET", "/franca/imoveis/atualizar"), ("POST", "/franca/precospublicos"),
    ("GET", "/franca/precospublicos"), ("GET", "/franca/consultaprecopublico"), ("GET", "/franca/parcelas"),
    ("GET", "/franca/Imoveis/Atualizar"), ("GET", "/franca/imoveis/%61tualizar"), ("GET", "/franca/imoveis/../precospublicos"),
    ("GET", "/franca/preços-públicos"), ("PUT", "/franca/imoveis"), ("DELETE", "/franca/empresas"),
    ("PATCH", "/franca/atividade"), ("POST", "/franca/imoveis"), ("GET", "/api/authenticate"),
    ("GET", "/franca/outra"), ("GET", "/franca/imoveis?InscrFisico=1"), ("GET", "franca/imoveis"),
])
def test_rotas_bloqueadas_antes_da_rede(cliente, metodo, caminho):
    c, estado, _ = cliente
    with pytest.raises(OperacaoProibida):
        checa_operacao(metodo, caminho)
    if metodo == "GET":
        with pytest.raises(OperacaoProibida):
            c.get(caminho, {"x": "1"})
    assert estado.req == [], "nenhuma requisição (nem a de token) pode sair para rota bloqueada"


def test_allowlist_exata():
    assert checa_operacao("POST", "/api/authenticate") == "/api/authenticate"
    for c in ("/franca/imoveis", "/franca/empresas", "/franca/atividade", "/franca/empresa/consultarlote"):
        assert checa_operacao("GET", c) == c
    from app.connectors.sil import cliente as mod
    assert mod.GET_PERMITIDOS == {"/franca/imoveis", "/franca/empresas", "/franca/atividade",
                                  "/franca/empresa/consultarlote"}
    assert {"/franca/imoveis/atualizar", "/franca/precospublicos", "/franca/consultaprecopublico",
            "/franca/parcelas"} <= mod.ROTAS_BLOQUEADAS


def test_codigo_sem_escrita_nem_rota_proibida():
    """SIL3/SIL13: nenhum POST/PUT/PATCH/DELETE além da autenticação, e as rotas proibidas só aparecem como literais
    dentro das listas de bloqueio (docstrings e comentários à parte)."""
    import ast
    listas = {"ROTAS_BLOQUEADAS", "SEGMENTOS_PROIBIDOS", "RADICAIS_PROIBIDOS"}
    proibida = re.compile(r"atualizar|preco|parcelas", re.I)
    for f in PACOTE.glob("*.py"):
        src = f.read_text(encoding="utf-8")
        assert not re.search(r"\.(put|patch|delete|post)\(", src, re.I), f.name
        arvore = ast.parse(src)
        permitidos = set()
        for n in ast.walk(arvore):
            if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id in listas for t in n.targets):
                permitidos |= {id(c) for c in ast.walk(n.value) if isinstance(c, ast.Constant)}
            if isinstance(n, (ast.Module, ast.FunctionDef, ast.ClassDef)) and n.body and \
                    isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant):
                permitidos.add(id(n.body[0].value))
        for c in ast.walk(arvore):
            if isinstance(c, ast.Constant) and isinstance(c.value, str) and id(c) not in permitidos:
                if proibida.search(c.value) and "proibida" not in c.value:
                    pytest.fail(f"{f.name}:{c.lineno}: rota proibida referenciada fora das listas de bloqueio")
                assert c.value.upper() not in ("PUT", "PATCH", "DELETE"), f"{f.name}:{c.lineno}"
    src = (PACOTE / "cliente.py").read_text(encoding="utf-8")
    # "POST" só em: a regra de checa_operacao, a checagem do AUTH_PATH em _autentica e o único envio (o token)
    assert src.count('"POST"') == 3
    assert src.count('self._envia("POST"') == 1 and 'checa_operacao("POST", AUTH_PATH)' in src


def test_endpoint_plugavel_so_aceita_get_permitido():
    with pytest.raises(OperacaoProibida):
        registrar(EndpointSil("zz", "/franca/parcelas", "idparcela", "x", "zz"))
    with pytest.raises(OperacaoProibida):
        registrar(EndpointSil("zz", "/franca/consultaprecopublico", "x", "x", "zz"))
    assert "zz" not in ENDPOINTS
    assert {"imoveis", "empresas", "atividade", "empresas_lote"} <= set(ENDPOINTS)
    assert endpoint("empresas_lote").fixos == (("Status", "Ativo"),) and endpoint("empresas_lote").paginado


# ---------- SIL2: token ----------
def test_token_em_cache_e_renovado_antes_de_expirar(cliente):
    c, estado, rel = cliente
    for _ in range(3):
        assert c.get("/franca/imoveis", {"InscrFisico": "ZZT1"}) is not None
    assert estado.conta("POST", "/api/authenticate") == 1 and c.ambiente == "producao"
    auth = next(p for m, cam, p in estado.req if m == "POST")
    assert auth["grant_type"] == "client_credentials" and auth["scope"] == "zzteste"
    rel.t += 600 - 60 + 1  # passou da margem (expires_in 600, renova 60s antes)
    c.get("/franca/imoveis", {"InscrFisico": "ZZT1"})
    assert estado.conta("POST", "/api/authenticate") == 2


def test_401_renova_uma_vez_e_repete(cliente):
    c, estado, _ = cliente
    c.get("/franca/imoveis", {"InscrFisico": "ZZT1"})
    estado.respostas_forcadas = [(401, {"status": "Falha"}, {})]
    assert c.get("/franca/imoveis", {"InscrFisico": "ZZT1"}) is not None
    assert estado.conta("POST", "/api/authenticate") == 2


def test_segundo_401_vira_falha_credencial(cliente):
    c, estado, _ = cliente
    estado.respostas_forcadas = [(401, {}, {}), (401, {}, {})]
    with pytest.raises(FalhaCredencial):
        c.get("/franca/imoveis", {"InscrFisico": "ZZT1"})


def test_credencial_recusada_na_autenticacao(cliente):
    c, estado, _ = cliente
    estado.auth_ok = False
    with pytest.raises(FalhaCredencial):
        c.get("/franca/imoveis", {"InscrFisico": "ZZT1"})


# ---------- TL 27/10: sondagem sandbox -> produção ----------
def test_sondagem_tenta_sandbox_primeiro_e_cai_para_producao(mock):
    estado_prod, url_prod = mock
    est_sb = EstadoMock()
    est_sb.auth_ok = False  # homologação recusa
    sb = servidor(est_sb)
    try:
        rel = Relogio()
        c = ClienteSil(cfg(url_prod, base_url_sandbox=f"http://127.0.0.1:{sb.server_address[1]}"), relogio=rel,
                       dormir=rel.dormir)
        assert c.sondar() == "producao"
        assert est_sb.conta("POST", "/api/authenticate") == 1 and estado_prod.conta("POST", "/api/authenticate") == 1
        c.get("/franca/imoveis", {"InscrFisico": "ZZT1"})
        assert estado_prod.conta("GET", "/franca/imoveis") == 1 and est_sb.conta("GET", "/franca/imoveis") == 0
        c.fechar()
        # sandbox aceita: fica na homologação e não toca a produção
        est_sb.auth_ok = True
        estado_prod.req.clear()
        c = ClienteSil(cfg(url_prod, base_url_sandbox=f"http://127.0.0.1:{sb.server_address[1]}"), relogio=rel,
                       dormir=rel.dormir)
        c.get("/franca/imoveis", {"InscrFisico": "ZZT1"})
        assert c.ambiente == "sandbox" and estado_prod.req == []
        c.fechar()
    finally:
        sb.shutdown()


def test_sandbox_fora_do_ar_cai_para_producao(mock):
    estado, url = mock
    rel = Relogio()
    c = ClienteSil(cfg(url, base_url_sandbox="http://127.0.0.1:9", tentativas=0), relogio=rel, dormir=rel.dormir)
    assert c.sondar() == "producao"
    c.fechar()


# ---------- SIL5: retry/backoff/timeout ----------
def test_retry_com_backoff_em_429_e_500(cliente):
    c, estado, rel = cliente
    c.sondar()
    estado.respostas_forcadas = [(429, {}, {"Retry-After": "3"}), (503, {}, {})]
    assert c.get("/franca/imoveis", {"InscrFisico": "ZZT1"}) is not None
    assert estado.conta("GET", "/franca/imoveis") == 3
    assert 3.0 in rel.sonos  # Retry-After respeitado


def test_esgota_tentativas_vira_falha(cliente):
    c, estado, _ = cliente
    c.sondar()
    estado.respostas_forcadas = [(500, {}, {})] * 3
    with pytest.raises(FalhaSil) as e:
        c.get("/franca/imoveis", {"InscrFisico": "ZZT1"})
    assert e.value.status == 500 and estado.conta("GET", "/franca/imoveis") == 3


def test_timeout(mock):
    estado, url = mock
    rel = Relogio()
    c = ClienteSil(cfg(url, timeout_s=0.3, tentativas=1), relogio=rel, dormir=rel.dormir)
    c.sondar()
    estado.atraso_s = 1.0
    with pytest.raises(FalhaSil) as e:
        c.get("/franca/imoveis", {"InscrFisico": "ZZT1"})
    assert "timeout" in str(e.value) and e.value.__cause__ is None and e.value.__context__ is None
    c.fechar()


def test_404_e_nao_localizado(cliente):
    c, _, _ = cliente
    assert c.get("/franca/imoveis", {"InscrFisico": "ZZ404X"}) is None


def test_throttle():
    rel = Relogio()
    c = ClienteSil(cfg("http://127.0.0.1:9", req_por_s=2.0), transport=httpx.MockTransport(
        lambda r: httpx.Response(200, json={"access_token": "zz", "expires_in": 600} if r.method == "POST" else {})),
        relogio=rel, dormir=rel.dormir)
    for _ in range(4):
        c.get("/franca/atividade", {"codatividade": "ZZ"})
    assert sum(rel.sonos) == pytest.approx(0.5 * 4, abs=0.01)  # 1 auth + 4 GET a 2 req/s


# ---------- logs sem segredo, token, chave ou query ----------
def test_logs_sem_segredo_token_nem_chave(cliente, caplog):
    c, estado, _ = cliente
    doc = _cpf(900000001)
    caplog.set_level(logging.DEBUG)
    c.get("/franca/empresas", {"Documento": doc})
    estado.respostas_forcadas = [(500, {}, {})] * 3
    with pytest.raises(FalhaSil):
        c.get("/franca/empresas", {"Documento": doc})
    texto = caplog.text
    for proibido in (SEGREDO_SINTETICO, ID_SINTETICO, TOKEN, doc, "Documento=", "127.0.0.1"):
        assert proibido not in texto, proibido
    assert "/franca/empresas" in texto


# ---------- SIL11: consultarlote ----------
def test_consultarlote_um_lote_por_padrao(cliente, monkeypatch):
    c, estado, _ = cliente
    monkeypatch.delenv("SIL_CONSULTARLOTE_MAX_LOTES", raising=False)
    r = carga.paginar_lote(c, endpoint("empresas_lote"))
    gets = [p for m, cam, p in estado.req if cam == "/franca/empresa/consultarlote"]
    assert len(gets) == 1 and gets[0]["Sequencia"] == "0" and gets[0]["Status"] == "Ativo"
    assert r["contagem"]["lotes"] == 1 and len(r["registros"]) == estado.tamanho_lote
    assert r["contagem"]["proxima_sequencia"] == 300 and r["contagem"]["fim"] == "max_lotes"


def test_consultarlote_pagina_pelo_maior_id_teto_1000_sem_repetir(cliente):
    c, estado, _ = cliente
    estado.ids_repetidos = True
    r = carga.paginar_lote(c, endpoint("empresas_lote"), max_lotes=50, limite=5000)
    seqs = [int(p["Sequencia"]) for m, cam, p in estado.req if cam == "/franca/empresa/consultarlote"]
    assert seqs[:4] == [0, 300, 600, 900]
    ids = [x["id_lote"] for x in r["registros"]]
    assert len(ids) == 1000 == len(set(ids)), "teto 1000 na amostra e nenhum ID repetido"
    assert r["contagem"]["repetidos"] >= 1 and r["contagem"]["fim"] == "limite"


def test_consultarlote_para_em_404_e_lote_vazio(cliente):
    c, estado, _ = cliente
    estado.empresas = {i: estado.empresas[i] for i in range(1, 451)}
    r = carga.paginar_lote(c, endpoint("empresas_lote"), max_lotes=10, limite=1000)
    assert len(r["registros"]) == 450 and r["contagem"]["fim"] == "404" and r["contagem"]["lotes"] == 3
    estado.req.clear()
    estado.respostas_forcadas = [(200, {"data": {"empresas": []}, "status": "Sucesso"}, {})]
    r = carga.paginar_lote(c, endpoint("empresas_lote"), max_lotes=10)
    assert r["registros"] == [] and r["contagem"]["fim"] == "lote_vazio"


def test_consultarlote_max_lotes_so_por_configuracao(cliente, monkeypatch):
    c, estado, _ = cliente
    monkeypatch.setenv("SIL_CONSULTARLOTE_MAX_LOTES", "2")
    r = carga.paginar_lote(c, endpoint("empresas_lote"))
    assert r["contagem"]["lotes"] == 2 and len(r["registros"]) == 600


# ---------- SIL4/SIL5: lote por chave ----------
def test_lote_por_chave_conta_e_para_apos_3_falhas(cliente):
    c, estado, _ = cliente
    r = carga.consultar_lote(c, endpoint("imoveis"), ["ZZT1", "ZZ404A", "ZZT2", ""])
    k = r["contagem"]
    assert (k["lidos"], k["localizados"], k["nao_localizados"], k["erros"]) == (3, 2, 1, 0)
    estado.respostas_forcadas = [(500, {}, {})] * 9
    r = carga.consultar_lote(c, endpoint("imoveis"), [f"ZZT{i}" for i in range(10)])
    assert r["contagem"]["parou"] and r["contagem"]["erros"] == 3 and r["contagem"]["lidos"] == 3


# ---------- SIL6/SIL8/SIL11: prata, débito e quem vê documento ----------
def test_prata_debito_e_documento_so_admin_lgpd():
    cpf = _cpf(900000003)
    e = prata.projeta_empresa({"Ccm": "3", "CNPJCPF": f"{cpf[:3]}.{cpf[3:6]}.{cpf[6:9]}-{cpf[9:]}",
                               "PossuiDebito": "S", "Nome": "ZZTESTE", "Email": "zz@zzteste.invalid",
                               "Telefone": "0000"})
    assert e["documento"] == cpf and e["cidadao_id"] and e["possui_debito"] is True and e["pendencia"] is None
    for perfil in ("admin", "operador", "leitura"):
        v = prata.visao(e, perfil)
        assert cpf not in str(v) and v["email"] == v["telefone"] == "••• (oculto)"
    assert prata.visao(e, "admin_lgpd")["documento"] == cpf
    assert prata.projeta_empresa({"CNPJCPF": "123", "PossuiDebito": "x"})["pendencia"] == "documento_invalido"
    assert prata.ouro(e) == {"cidadao_id": e["cidadao_id"], "possui_debito": True}
    i = prata.projeta_imovel({"CNPJCPFResponsavel": cpf, "PossuiDebito": "N"})
    assert i["possui_debito"] is False and i["cidadao_id_responsavel"] == e["cidadao_id"]
    assert set(prata.ouro(i)) == {"cidadao_id", "possui_debito"}  # nada de valor financeiro


# ---------- bronze (banco de teste) ----------
def test_bronze_consultarlote_e_por_chave(db, cliente, monkeypatch):
    from sqlalchemy import select

    from app.models.camadas import RegistroBronze
    c, estado, _ = cliente
    monkeypatch.delenv("SIL_CONSULTARLOTE_MAX_LOTES", raising=False)
    ex = carga.executar_enriquecimento(db, "admin@aion.local", endpoint("empresas_lote"), cliente=c)
    assert ex.status == "sucesso" and ex.lidos == 300 and ex.novos == 300, ex.mensagem
    regs = list(db.scalars(select(RegistroBronze).where(RegistroBronze.sistema_origem == "SIL",
                                                        RegistroBronze.tabela == "empresas")))
    assert len(regs) == 300
    for r in regs[:20]:
        assert r.dados["resposta"]["CNPJCPF"] not in r.id_origem and r.id_origem.startswith("empresas_lote:")
    assert _cpf(900000001) not in (ex.mensagem or "")
    ex2 = carga.executar_enriquecimento(db, "admin@aion.local", endpoint("empresas_lote"), cliente=c)
    assert ex2.novos == 0 and ex2.ignorados == 300  # repetir não duplica
    ex3 = carga.executar_enriquecimento(db, "admin@aion.local", endpoint("imoveis"), ["ZZT9", "ZZ404B"], cliente=c)
    assert ex3.status == "sucesso" and "nao_localizados=1" in ex3.mensagem and "ZZT9" not in ex3.mensagem
    with pytest.raises(ValueError):
        carga.executar_enriquecimento(db, "admin@aion.local", endpoint("imoveis"), ["x"] * 1001, cliente=c)


def test_rota_status_sem_segredo(client, tokens, monkeypatch):
    monkeypatch.setenv("SIL_BASE_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("SIL_CLIENT_ID", ID_SINTETICO)
    monkeypatch.setenv("SIL_CLIENT_SECRET", SEGREDO_SINTETICO)
    r = client.get("/api/conectores/sil/status", headers=tokens["admin"])
    assert r.status_code == 200 and r.json()["habilitado"] is True
    assert SEGREDO_SINTETICO not in r.text and ID_SINTETICO not in r.text and "127.0.0.1" not in r.text
    assert set(r.json()["endpoints"]) == {"imoveis", "empresas", "atividade", "empresas_lote"}
    assert client.get("/api/conectores/sil/status", headers=tokens["leitura"]).status_code == 403
