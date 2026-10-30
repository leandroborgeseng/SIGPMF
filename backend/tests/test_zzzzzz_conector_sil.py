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
    ErroSil,
    FalhaCredencial,
    FalhaSil,
    OperacaoProibida,
    RespostaAuthInvalida,
    SilDesabilitado,
    checa_operacao,
    parse_expires_in,
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
    # R2 (QA #23): além de .post( também .request(, .send( e .stream( (e put/patch/delete). Unificação: o único ponto
    # de rede do pacote é o self._http.request(...) do ClienteSil._envia, sempre depois de checa_operacao.
    escrita = re.compile(r"\.(?:put|patch|delete|post|request|send|stream)\(", re.I)
    for f in PACOTE.glob("*.py"):
        src = f.read_text(encoding="utf-8")
        achados = escrita.findall(src)
        if f.name == "cliente.py":
            assert achados == [".request("] and "self._http.request(metodo, caminho" in src, (f.name, achados)
        else:
            assert achados == [], (f.name, achados)
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


# ---------- TL 29/10: oficial por padrão; sandbox só com usar_sandbox; nunca fallback ----------
def test_sondagem_sandbox_aceita_nao_toca_producao(mock):
    estado_prod, url_prod = mock
    est_sb = EstadoMock()
    sb = servidor(est_sb)
    try:
        rel = Relogio()
        c = ClienteSil(cfg(url_prod, base_url_sandbox=f"http://127.0.0.1:{sb.server_address[1]}", usar_sandbox=True),
                       relogio=rel,
                       dormir=rel.dormir)
        c.get("/franca/imoveis", {"InscrFisico": "ZZT1"})
        assert c.ambiente == "sandbox" and estado_prod.req == [] and est_sb.conta("GET", "/franca/imoveis") == 1
        c.fechar()
    finally:
        sb.shutdown()


def test_sandbox_configurado_sem_flag_usa_so_a_oficial(mock):
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


# ---------- SIL9 / TL 27/10: disparo pela API (admin, auditado, 202 + background, um lote) ----------
@pytest.fixture
def sil_env(mock, monkeypatch):
    estado, url = mock
    monkeypatch.setenv("SIL_BASE_URL", url)
    monkeypatch.delenv("SIL_BASE_URL_SANDBOX", raising=False)
    monkeypatch.setenv("SIL_CLIENT_ID", ID_SINTETICO)
    monkeypatch.setenv("SIL_CLIENT_SECRET", SEGREDO_SINTETICO)
    monkeypatch.setenv("SIL_REQ_POR_S", "20")
    monkeypatch.setenv("SIL_CONSULTARLOTE_MAX_LOTES", "5")  # a rota força 1 lote mesmo assim
    return estado


@pytest.fixture
def sil_sincrono(monkeypatch):
    from app.services import amostra_sil as servico
    monkeypatch.setattr(servico, "disparar", lambda ex_id, usuario, nome, trava: servico.rodar(ex_id, usuario, nome, trava))


def _auditoria(db, acao, alvo=None):
    from sqlalchemy import select

    from app.models.controle import Auditoria
    db.expire_all()
    q = select(Auditoria).where(Auditoria.acao == acao)
    if alvo:
        q = q.where(Auditoria.alvo == alvo)
    return list(db.scalars(q.order_by(Auditoria.id)))


def test_amostra_sil_403_para_quem_nao_e_admin(client, tokens, db, sil_env, sil_sincrono):
    assert client.post("/api/conectores/sil/amostra").status_code == 401
    antes = len(_auditoria(db, "acesso_negado", "/api/conectores/sil/amostra"))
    for perfil in ("leitura", "operador", "admin_lgpd"):
        assert client.post("/api/conectores/sil/amostra", headers=tokens[perfil]).status_code == 403
    assert len(_auditoria(db, "acesso_negado", "/api/conectores/sil/amostra")) == antes + 3
    assert sil_env.req == []  # nenhuma chamada à SIL


def test_amostra_sil_admin_um_lote_e_auditoria(client, tokens, db, sil_env, sil_sincrono, monkeypatch):
    from app.models.controle import Execucao
    monkeypatch.setenv("SIL_AMOSTRA_MAX_LOTES", "1")  # PR-A: padrão 20 (teste próprio); aqui fixa 1 lote
    r = client.post("/api/conectores/sil/amostra", headers=tokens["admin"])
    assert r.status_code == 202, r.text
    j = r.json()
    assert j["status"] in ("sucesso", "sem_novidades") and j["lotes"] == 1 and j["lidos"] == sil_env.tamanho_lote
    lotes = [p for m, c, p in sil_env.req if c == "/franca/empresa/consultarlote"]
    assert len(lotes) == 1 and lotes[0]["Sequencia"] == "0" and lotes[0]["Status"] == "Ativo"
    ex = db.get(Execucao, j["id"])
    assert ex.usuario == "admin@aion.local" and ex.finalizado_em and ex.etapa == "concluida"
    alvo = f"execucao:{j['id']}"
    ini, fim = _auditoria(db, "amostra_sil_iniciada", alvo), _auditoria(db, "amostra_sil_executada", alvo)
    assert len(ini) == 1 and len(fim) == 1
    for a in ini + fim + _auditoria(db, "amostra_sil_solicitada")[-1:]:
        assert a.usuario == "admin@aion.local" and a.criado_em is not None
        txt = f"{a.alvo} {a.detalhe}"
        assert not re.search(r"\d{11}", txt) and SEGREDO_SINTETICO not in txt and "127.0.0.1" not in txt
    assert fim[0].detalhe["lotes"] == 1 and fim[0].detalhe["status"] == j["status"]
    s = client.get(f"/api/conectores/sil/amostra/{j['id']}", headers=tokens["admin"])
    assert s.status_code == 200 and s.json()["em_andamento"] is False
    assert client.get(f"/api/conectores/sil/amostra/{j['id']}", headers=tokens["operador"]).status_code == 403
    assert client.get("/api/conectores/sil/amostra/999999", headers=tokens["admin"]).status_code == 404


def test_amostra_sil_409_enquanto_roda(client, tokens, db, sil_env, monkeypatch):
    """Thread real: a SIL sintética demora; o 2º pedido recebe 409 e nada é interrompido; no fim a trava é solta."""
    monkeypatch.setenv("SIL_AMOSTRA_MAX_LOTES", "1")  # PR-A: padrão 20 (teste próprio); aqui fixa 1 lote
    import time
    sil_env.atraso_s = 1.5
    t0 = time.monotonic()
    r = client.post("/api/conectores/sil/amostra", headers=tokens["admin"])
    assert r.status_code == 202 and r.json()["em_andamento"] is True and time.monotonic() - t0 < 1.0
    eid = r.json()["id"]
    r2 = client.post("/api/conectores/sil/amostra", headers=tokens["admin"])
    assert r2.status_code == 409 and f"execução {eid}" in r2.json()["detail"]
    assert _auditoria(db, "amostra_sil_conflito")[-1].usuario == "admin@aion.local"
    for _ in range(100):
        j = client.get(f"/api/conectores/sil/amostra/{eid}", headers=tokens["admin"]).json()
        if not j["em_andamento"]:
            break
        time.sleep(0.1)
    assert j["status"] in ("sucesso", "sem_novidades") and j["lotes"] == 1, j
    sil_env.atraso_s = 0
    for _ in range(20):  # a thread solta a trava logo depois de gravar
        r3 = client.post("/api/conectores/sil/amostra", headers=tokens["admin"])
        if r3.status_code != 409:
            break
        time.sleep(0.1)
    assert r3.status_code == 202
    for _ in range(50):
        if not client.get(f"/api/conectores/sil/amostra/{r3.json()['id']}", headers=tokens["admin"]).json()["em_andamento"]:
            break
        time.sleep(0.1)


def test_amostra_sil_desligada_409_sem_chamar(client, tokens, monkeypatch, sil_sincrono, db):
    for v in ("SIL_BASE_URL", "SIL_BASE_URL_SANDBOX", "SIL_CLIENT_ID", "SIL_CLIENT_SECRET"):
        monkeypatch.delenv(v, raising=False)
    r = client.post("/api/conectores/sil/amostra", headers=tokens["admin"])
    assert r.status_code == 409 and "desligado" in r.json()["detail"]
    assert _auditoria(db, "amostra_sil_recusada")[-1].detalhe == {"motivo": "desligado"}


# ---------- QA PR23 ciclo 1: expires_in "600s" e sem queda para produção ----------
def _sondagem_mock(valor):
    vistos = []

    def h(r):
        vistos.append((r.url.host, r.method, r.url.path))
        if r.method == "POST":
            return httpx.Response(200, json={"access_token": "ZZTOKEN", "token_type": "Bearer", "expires_in": valor})
        return httpx.Response(200, json={"data": {}})
    rel = Relogio()
    c = ClienteSil(ConfigSil(base_url="https://prod.zzteste.invalid/v1", client_id="zz-id", client_secret="zz-secret",
                             scope="zz", base_url_sandbox="https://sandbox.zzteste.invalid/v1", usar_sandbox=True),
                   transport=httpx.MockTransport(h), relogio=rel, dormir=rel.dormir)
    return c, rel, vistos


@pytest.mark.parametrize("valor", [600, "600", "600s", " 600s ", "600 s", 600.0])
def test_expires_in_formatos_aceitos_fica_no_sandbox(valor):
    c, rel, vistos = _sondagem_mock(valor)
    c.get("/franca/imoveis", {"InscrFisico": "000"})
    assert c.ambiente == "sandbox"
    assert round(c._expira_em - rel.t) == 600 - c.config.margem_token_s
    assert all(h == "sandbox.zzteste.invalid" for h, _, _ in vistos)


@pytest.mark.parametrize("valor", ["dez minutos", "s", "-5s", "0", [600], True, "nan"])
def test_expires_in_invalido_erro_claro_sem_producao(valor):
    c, _, vistos = _sondagem_mock(valor)
    with pytest.raises(RespostaAuthInvalida, match="expires_in"):
        c.sondar()
    assert c.ambiente is None
    assert vistos and all(h == "sandbox.zzteste.invalid" for h, _, _ in vistos)  # nunca tenta a produção


def test_parse_expires_in_ausente_assume_600():
    assert parse_expires_in(None) == 600.0 and parse_expires_in("") == 600.0 and parse_expires_in("120S") == 120.0


def test_amostra_sil_falha_marca_etapa_falha(client, tokens, db, sil_env, sil_sincrono):
    """QA PR23 ciclo 1: execução que falha não pode ficar com etapa 'concluida'."""
    from app.models.controle import Execucao
    sil_env.respostas_forcadas = [(500, {"status": "Falha"}, {})] * 20
    r = client.post("/api/conectores/sil/amostra", headers=tokens["admin"])
    assert r.status_code == 202, r.text
    ex = db.get(Execucao, r.json()["id"])
    db.refresh(ex)
    assert ex.status == "falha" and ex.etapa == "falha"


def test_amostra_sil_excecao_marca_etapa_falha(client, tokens, db, sil_env, sil_sincrono, monkeypatch):
    from app.models.controle import Execucao

    def explode(*a, **k):
        raise RuntimeError("zz")
    monkeypatch.setattr(carga, "executar_enriquecimento", explode)
    r = client.post("/api/conectores/sil/amostra", headers=tokens["admin"])
    ex = db.get(Execucao, r.json()["id"])
    db.refresh(ex)
    assert ex.status == "falha" and ex.etapa == "falha"


def _sondagem_rede(sandbox):
    """Transporte mock: 'sandbox' decide a resposta da homologação; a produção sempre autentica."""
    vistos = []

    def h(r):
        vistos.append(r.url.host)
        if r.url.host == "sandbox.zzteste.invalid":
            if isinstance(sandbox, Exception):
                raise sandbox
            return httpx.Response(sandbox, json={"status": "Falha"})
        return httpx.Response(200, json={"access_token": "ZZTOKEN", "expires_in": "600s"})
    rel = Relogio()
    c = ClienteSil(ConfigSil(base_url="https://prod.zzteste.invalid/v1", client_id="zz-id",
                             client_secret=SEGREDO_SINTETICO, scope="zz",
                             base_url_sandbox="https://sandbox.zzteste.invalid/v1", tentativas=0, usar_sandbox=True),
                   transport=httpx.MockTransport(h), relogio=rel, dormir=rel.dormir)
    return c, vistos


@pytest.mark.parametrize("status", [401, 403])
def test_sandbox_401_403_nao_tenta_producao(status):
    c, vistos = _sondagem_rede(status)
    with pytest.raises(FalhaCredencial, match="produção não foi tentada"):
        c.sondar()
    assert c.ambiente is None and vistos == ["sandbox.zzteste.invalid"]


def test_sandbox_500_nao_tenta_producao():
    c, vistos = _sondagem_rede(500)
    with pytest.raises(FalhaSil):
        c.sondar()
    assert set(vistos) == {"sandbox.zzteste.invalid"}


@pytest.mark.parametrize("erro", [httpx.ReadTimeout("zz https://sandbox.zzteste.invalid/v1?x=1"),
                                  httpx.ConnectError("zz https://sandbox.zzteste.invalid/v1")])
def test_sandbox_rede_nao_cai_para_producao_com_warning_limpo(erro, caplog):
    c, vistos = _sondagem_rede(erro)
    with caplog.at_level(logging.DEBUG, logger="sigpmf.conector.sil"):
        with pytest.raises(ErroSil):
            c.sondar()
    assert vistos == ["sandbox.zzteste.invalid"]                    # TL 29/10: nunca fallback automático
    avisos = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(avisos) == 1 and "sandbox" in avisos[0].getMessage() and "tentando" not in avisos[0].getMessage()
    tudo = " ".join(r.getMessage() for r in caplog.records)
    for proibido in ("zzteste.invalid", "http", SEGREDO_SINTETICO, "zz-id", "ZZTOKEN"):
        assert proibido not in tudo
