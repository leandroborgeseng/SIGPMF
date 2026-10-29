"""Unificação do conector SIL (TL 28/10): o leitor da carga genérica só fala com a SIL pelo ClienteSil.

- allowlist em toda chamada (nenhuma requisição sai para rota fora dela);
- homologação (sandbox) primeiro, sem cair para a produção em 401/403/4xx/5xx;
- rota genérica POST /conectores/<id numérico da base "API SIL">/amostra (caminho do QA do #23) passa pelo cliente
  unificado, com auditoria;
- carga completa e agenda leem no máximo SIL_MAX_LOTES_POR_EXECUCAO lotes (padrão 1);
- secret vazio: nenhuma chamada de rede.
Nenhuma rede real: só httpx.MockTransport e hosts .invalid inventados. Nenhuma credencial real.
"""
import logging
import re
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
import pytest
from sqlalchemy import func, select

from app.connectors.sil.cliente import OperacaoProibida
from app.connectors.sil.conector import ConfigSil, ErroSil, SilConnector
from app.core import recursos
from app.core.config import get_settings
from app.models.controle import Auditoria, BaseCatalogo, Execucao
from app.services import carga_completa as ccomp
from app.services import carga_conector as cc

SECRET = "zz-segredo-sintetico-unif"
SANDBOX, PROD = "sandbox.zzunif.invalid", "prod.zzunif.invalid"


class Api:
    """SIL simulada: registra (host, método, caminho) de cada requisição."""

    def __init__(self, ccms=range(1, 151), auth_status=None, rede_sandbox=False):
        self.ccms, self.auth_status, self.rede_sandbox = sorted(ccms), auth_status or {}, rede_sandbox
        self.hits: list[tuple[str, str, str]] = []
        self.sequencias: list[int] = []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        host, path = req.url.host, req.url.path
        self.hits.append((host, req.method, path))
        if self.rede_sandbox and host == SANDBOX:
            raise httpx.ConnectError("zz sem rede")
        if path.endswith("/api/authenticate"):
            st = self.auth_status.get(host, 200)
            if st != 200:
                return httpx.Response(st, json={"error": "zz"})
            return httpx.Response(200, json={"access_token": f"ZZTOKEN-{host}", "expires_in": "600s"})
        seq = int(req.url.params["Sequencia"])
        self.sequencias.append(seq)
        lote = [c for c in self.ccms if c >= seq][:50]
        return httpx.Response(200, json={"empresas": [{"empresa": {"Ccm": str(c), "Nome": f"EMPRESA ZZ {c}"}}
                                                      for c in lote]})

    def gets(self):
        return [h for h in self.hits if h[1] == "GET"]


def _cfg(sandbox=True, secret=SECRET, max_lotes=1):
    return ConfigSil(f"https://{PROD}/v1", "zz-id", secret, "zz", base_url_sandbox=f"https://{SANDBOX}/v1" if sandbox
                     else None, req_por_s=20.0, max_lotes_por_execucao=max_lotes,
                     usar_sandbox=sandbox)


def _con(api, **kw):
    return SilConnector(_cfg(**kw), transporte=httpx.MockTransport(api), esperar=lambda s: None, amostra_limite=1000)


# ---------------------------------------------------------------- leitor unificado

def test_leitor_passa_pela_allowlist_antes_da_rede():
    api = Api()
    con = _con(api)
    s = con.abrir()
    try:
        for caminho in ("/franca/imoveis/atualizar", "/franca/parcelas", "/franca/precospublicos", "/qualquer"):
            with pytest.raises(OperacaoProibida):
                s.cliente.get(caminho, {})
    finally:
        s.close()
    assert api.hits == []  # nem o token saiu


def test_leitor_usa_sandbox_primeiro_e_nao_toca_producao():
    api = Api()
    rows = _con(api).ler_amostra("Empresas")
    assert [r["Ccm"] for r in rows] == list(range(1, 51))           # 1 lote (teto padrão)
    assert {h[0] for h in api.hits} == {SANDBOX}
    assert {(m, p) for _h, m, p in api.hits} == {("POST", "/v1/api/authenticate"),
                                                ("GET", "/v1/franca/empresa/consultarlote")}


@pytest.mark.parametrize("status", [400, 401, 403, 429, 500])
def test_leitor_sem_queda_para_producao_em_resposta_http(status):
    api = Api(auth_status={SANDBOX: status})
    with pytest.raises(ErroSil):
        _con(api).ler_amostra("Empresas")
    assert {h[0] for h in api.hits} == {SANDBOX}


def test_leitor_sandbox_fora_da_rede_nao_cai_para_producao():
    api = Api(rede_sandbox=True)
    with pytest.raises(ErroSil):
        _con(api).ler_amostra("Empresas")
    assert api.hits and {h[0] for h in api.hits} == {SANDBOX}


@pytest.mark.parametrize("secret", [None, ""])
def test_secret_vazio_nenhuma_chamada_de_rede(secret):
    api = Api()
    con = _con(api, secret=secret)
    with pytest.raises(ErroSil, match="API SIL não configurada"):
        con.abrir()
    assert con.testar_conexao()["ok"] is False and api.hits == []


def test_secret_vazio_no_ambiente(monkeypatch):
    monkeypatch.setenv("SIL_BASE_URL", f"https://{PROD}/v1")
    monkeypatch.setenv("SIL_CLIENT_ID", "zz-id")
    monkeypatch.setenv("SIL_CLIENT_SECRET", "   ")
    assert ConfigSil.do_ambiente().habilitado is False


def test_teto_de_lotes_por_execucao_configuravel(monkeypatch):
    monkeypatch.delenv("SIL_MAX_LOTES_POR_EXECUCAO", raising=False)
    assert ConfigSil.do_ambiente().max_lotes_por_execucao == 1
    monkeypatch.setenv("SIL_MAX_LOTES_POR_EXECUCAO", "3")
    assert ConfigSil.do_ambiente().max_lotes_por_execucao == 3
    monkeypatch.setenv("SIL_MAX_LOTES_POR_EXECUCAO", "0")
    assert ConfigSil.do_ambiente().max_lotes_por_execucao == 1
    api = Api(ccms=range(1, 1000))
    con = _con(api, max_lotes=3)
    s = con.abrir()
    try:
        assert len(con.ler_pagina(s, "Empresas", None, ("Ccm",), None, 10_000)) == 150
        assert con.ler_pagina(s, "Empresas", None, ("Ccm",), (150,), 10_000) == []  # teto vale para a sessão toda
    finally:
        s.close()
    assert len(api.gets()) == 3


def test_retentativa_loga_info_e_so_a_falha_de_rede_e_warning(caplog):
    """R1 (QA #23): cada tentativa em INFO; a falha de rede vira 1 WARNING (sem queda para a produção)."""
    api = Api(rede_sandbox=True)
    with caplog.at_level(logging.DEBUG, logger="sigpmf.conector.sil"):
        with pytest.raises(ErroSil):
            _con(api).ler_amostra("Empresas")
    avisos = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(avisos) == 1 and "tentando o próximo" not in avisos[0]
    assert any("tentativa" in r.getMessage() and r.levelno == logging.INFO for r in caplog.records)
    tudo = caplog.text
    for proibido in ("zzunif.invalid", SECRET, "zz-id", "ZZTOKEN"):
        assert proibido not in tudo


def test_regex_r2_pega_request_send_stream():
    escrita = re.compile(r"\.(?:put|patch|delete|post|request|send|stream)\(", re.I)
    for amostra in ('c.post("x")', 'c.request("POST", u)', "c.send(req)", 'c.stream("GET", u)', "c.PUT(u)"):
        assert escrita.search(amostra), amostra
    assert not escrita.search('c.get("x")')


def test_conector_sem_httpx_direto():
    import inspect

    from app.connectors.sil import conector
    src = inspect.getsource(conector)
    assert "httpx.Client(" not in src and "_autentica" not in src and "ClienteSil(" in src


# ---------------------------------------------------------------- caminho do QA do #23 (rota genérica e agenda)

@pytest.fixture()
def sil_mock(monkeypatch):
    api = Api()
    monkeypatch.setenv("CARGA_COMPLETA_LOTE", "50")
    monkeypatch.setenv("CARGA_COMPLETA_PAUSA_MS", "0")
    monkeypatch.setattr(recursos, "disco", lambda: recursos.Uso(total=100, livre=50))
    get_settings.cache_clear()
    criados = []

    def leitor(conector, cfg):
        if conector != "sil":
            raise AssertionError("só a SIL neste teste")
        con = _con(api)
        criados.append(con)
        return con
    monkeypatch.setattr(cc, "leitor_de", leitor)

    def sincrono(ex_id, conector, trava, somente_camadas=False):
        ccomp.rodar(ex_id, conector, trava, somente_camadas=somente_camadas)
    monkeypatch.setattr(ccomp, "disparar", sincrono)
    yield api
    get_settings.cache_clear()


def test_rota_generica_por_id_numerico_passa_pelo_cliente_unificado(client, tokens, db, sil_mock):
    base = db.scalar(select(BaseCatalogo).where(BaseCatalogo.conjunto == "sil"))
    assert base is not None
    r = client.post(f"/api/conectores/{base.id}/amostra", headers=tokens["admin"])
    assert r.status_code == 202, r.text
    assert r.json()["status"] in ("sucesso", "sem_novidades"), r.json()
    assert {h[0] for h in sil_mock.hits} == {SANDBOX}                # homologação, nunca a produção
    assert {(m, p) for _h, m, p in sil_mock.hits} <= {("POST", "/v1/api/authenticate"),
                                                      ("GET", "/v1/franca/empresa/consultarlote")}
    assert len(sil_mock.gets()) == 1                                 # um lote
    aud = db.scalar(select(Auditoria).where(Auditoria.acao == "amostra_conector_iniciada")
                    .order_by(Auditoria.id.desc()).limit(1))
    assert aud.detalhe["conector"] == "sil" and aud.detalhe["cliente"] == "ClienteSil"
    assert aud.detalhe["max_lotes_por_execucao"] == 1


def _ultima_aud(db, acao):
    return db.scalar(select(Auditoria).where(Auditoria.acao == acao).order_by(Auditoria.id.desc()).limit(1))


def _checkpoint_sil(db):
    from app.models.controle import CheckpointCarga
    db.expire_all()
    from app.bronze.ingestao import ambiente_da_origem
    return db.scalar(select(CheckpointCarga).where(CheckpointCarga.sistema == "SIL", CheckpointCarga.tabela == "Empresas",
                                                   CheckpointCarga.ambiente == ambiente_da_origem("SIL")))


def _zera_checkpoint_sil(db):
    from sqlalchemy import update

    from app.models.controle import CheckpointCarga
    db.execute(update(CheckpointCarga).where(CheckpointCarga.sistema == "SIL")
               .values(posicao=0, concluida=False, ultima_chave=None))
    db.commit()


def test_amostra_no_teto_com_mais_dados_fica_parcial(db, sil_mock):
    a = cc.executar_amostra(db, "admin@aion.local", "sil")
    assert a.status in ("sucesso", "sem_novidades"), a.mensagem
    assert a.mensagem.startswith("parcial: 1 lote(s) lido(s), há mais")
    aud = _ultima_aud(db, "amostra_conector_parcial")
    assert aud.alvo == f"execucao:{a.id}" and aud.detalhe == {"conector": "sil", "parcial": True, "lotes_lidos": 1,
                                                              "ha_mais": True}


def test_carga_completa_no_teto_fica_parcial_e_nao_vira_completa(db, sil_mock, monkeypatch):
    from app.services import camadas_estado
    from app.silver import processamento
    a = cc.executar_amostra(db, "admin@aion.local", "sil")
    assert a.status in ("sucesso", "sem_novidades"), a.mensagem
    _zera_checkpoint_sil(db)
    estado_antes = camadas_estado.assinatura(db), db.scalar(select(func.count()).select_from(
        __import__("app.models.camadas", fromlist=["EmpresaSil"]).EmpresaSil))

    def proibido(*a, **k):
        raise AssertionError("execução parcial não reconstrói prata/ouro")
    monkeypatch.setattr(processamento, "reconstruir", proibido)
    antes = len(sil_mock.gets())
    ex = ccomp.iniciar(db, "admin@aion.local", "sil")
    db.expire_all()
    ex = db.get(Execucao, ex.id)
    assert ex.status == "parcial", ex.mensagem
    assert ex.mensagem.startswith("parcial: 1 lote(s) lido(s), há mais") and "reconciliacao" not in ex.mensagem
    assert len(sil_mock.gets()) - antes == 1 and {h[0] for h in sil_mock.hits} == {SANDBOX}
    cp = _checkpoint_sil(db)
    assert cp.concluida is False and cp.ultima_chave == [50]          # checkpoint aberto: continua do CCM 51
    from app.models.camadas import EmpresaSil
    assert db.scalar(select(func.count()).select_from(EmpresaSil)) == estado_antes[1]  # nada apagado da silver
    aud = _ultima_aud(db, "carga_completa_finalizada")
    assert aud.alvo == f"execucao:{ex.id}" and aud.detalhe["resultado"] == "parcial"
    assert aud.detalhe["parcial"] is True and aud.detalhe["lotes_lidos"] == 1 and aud.detalhe["ha_mais"] is True
    # a próxima execução continua de onde parou (Sequencia=51), ainda parcial
    ex2 = ccomp.iniciar(db, "admin@aion.local", "sil")
    db.expire_all()
    assert db.get(Execucao, ex2.id).status == "parcial"
    ultimo_get = [r for r in sil_mock.gets()][-1]
    assert ultimo_get[2].endswith("/consultarlote")
    assert _checkpoint_sil(db).ultima_chave == [100]
    _zera_checkpoint_sil(db)


def test_carga_completa_que_acaba_dentro_do_teto_e_completa(db, sil_mock):
    sil_mock.ccms = list(range(1, 31))                               # 30 empresas: um lote incompleto = fim
    a = cc.executar_amostra(db, "admin@aion.local", "sil")
    assert a.status in ("sucesso", "sem_novidades") and not a.mensagem.startswith("parcial")
    _zera_checkpoint_sil(db)
    ex = ccomp.iniciar(db, "admin@aion.local", "sil")
    db.expire_all()
    ex = db.get(Execucao, ex.id)
    assert ex.status == "sucesso" and "reconciliacao=" in ex.mensagem, ex.mensagem
    assert _checkpoint_sil(db).concluida is True
    _zera_checkpoint_sil(db)


def test_agenda_parcial_nao_marca_completa_nem_assinatura(db, sil_mock):
    from app.models.controle import Agendamento
    from app.services import agenda
    a = cc.executar_amostra(db, "admin@aion.local", "sil")
    assert a.status in ("sucesso", "sem_novidades"), a.mensagem
    _zera_checkpoint_sil(db)
    ag = db.scalar(select(Agendamento).where(Agendamento.conector == "sil"))
    assert ag is not None and ag.ativo is False                      # 0015: nasce desligado
    antes = (ag.ultima_completa_em, ag.assinatura_origem)
    n_get = len(sil_mock.gets())
    st = agenda.executar(db, ag)
    db.refresh(ag)
    assert st == "parcial", ag.ultima_mensagem
    assert len(sil_mock.gets()) - n_get == 1 and {h[0] for h in sil_mock.hits} == {SANDBOX}
    assert (ag.ultima_completa_em, ag.assinatura_origem) == antes   # IN3/AG3 não veem carga completa
    assert ag.ultimo_status == "parcial" and ag.ultima_mensagem.startswith("parcial:")
    if antes[0] is None:  # R3: parcial não marca completa, então a próxima segue sendo a primeira completa
        assert agenda.modo_da_vez(ag, datetime.now(ZoneInfo("UTC"))) == "completa"
    _zera_checkpoint_sil(db)


# ---------------------------------------------------------------- SIL15: agenda

def _agd(**kw):
    from app.models.controle import Agendamento
    base = dict(conector="sil", horario="05:30", ativo=False, ordem=6, forcar=False, ultima_data=None,
                tentativas=0, proxima_tentativa_em=None)
    base.update(kw)
    return Agendamento(**base)


def test_sil15_forcar_nao_liga_agendamento_desligado():
    from zoneinfo import ZoneInfo

    from app.services import agenda as ag
    ini, fim = ag.janela("00:00-06:00")
    meio_dia = datetime(2026, 10, 28, 12, 0, tzinfo=ZoneInfo("America/Sao_Paulo"))
    assert ag.devido(_agd(ativo=False, forcar=True), meio_dia, ini, fim) is False
    assert ag.devido(_agd(ativo=True, forcar=True), meio_dia, ini, fim) is True


# ---------------------------------------------------------------- SIL14, diagnóstico e executar-agora

@pytest.mark.parametrize("vazio", ["base_url", "client_id", "client_secret"])
@pytest.mark.parametrize("valor", [None, "", "   ", "\t\n"])
def test_sil14_qualquer_das_tres_vazia_ou_branca_recusa_sem_rede(monkeypatch, vazio, valor):
    """Mutante "só a URL é checada": cada uma das três variáveis, vazia ou só com espaços, desliga o conector."""
    env = {"base_url": ("SIL_BASE_URL", f"https://{PROD}/v1"), "client_id": ("SIL_CLIENT_ID", "zz-id"),
           "client_secret": ("SIL_CLIENT_SECRET", SECRET)}
    for k, (nome, v) in env.items():
        if k == vazio and valor is None:
            monkeypatch.delenv(nome, raising=False)
        else:
            monkeypatch.setenv(nome, valor if k == vazio else f"  {v}  ")
    monkeypatch.delenv("SIL_BASE_URL_SANDBOX", raising=False)
    cfg = ConfigSil.do_ambiente()
    assert cfg.habilitado is False and cfg.configurado() is False
    api = Api()
    con = SilConnector(cfg, transporte=httpx.MockTransport(api), esperar=lambda s: None, amostra_limite=10)
    with pytest.raises(ErroSil, match="API SIL não configurada"):
        con.abrir()
    assert con.diagnostico()["ok"] is False and api.hits == []


@pytest.mark.parametrize("campo", ["base_url", "client_id", "client_secret"])
def test_sil14_config_construida_com_espacos_tambem_desliga(campo):
    kw = {"base_url": f"https://{PROD}/v1", "client_id": "zz-id", "client_secret": SECRET, campo: "   "}
    assert ConfigSil(**kw).habilitado is False


def test_sil14_ambiente_com_espacos_em_volta_e_aparado(monkeypatch):
    monkeypatch.setenv("SIL_BASE_URL", f"  https://{PROD}/v1/  ")
    monkeypatch.setenv("SIL_CLIENT_ID", " zz-id ")
    monkeypatch.setenv("SIL_CLIENT_SECRET", f" {SECRET}\n")
    cfg = ConfigSil.do_ambiente()
    assert (cfg.base_url, cfg.client_id, cfg.client_secret) == (f"https://{PROD}/v1", "zz-id", SECRET)


def test_diagnostico_so_autentica_pelo_cliente_com_allowlist(monkeypatch):
    from app.connectors.sil import cliente
    vistos = []
    original = cliente.checa_operacao
    monkeypatch.setattr(cliente, "checa_operacao", lambda m, c: vistos.append((m, c)) or original(m, c))
    api = Api()
    assert _con(api).diagnostico()["ok"] is True
    assert vistos == [("POST", "/api/authenticate")]
    assert [(m, p) for _h, m, p in api.hits] == [("POST", "/v1/api/authenticate")]


def test_sil15_executar_agora_sil_desligado_409_auditado_sem_rede(client, tokens, db, monkeypatch, sil_mock):
    from app.models.controle import Agendamento
    monkeypatch.setenv("AGENDA_HABILITADA", "true")
    get_settings.cache_clear()
    try:
        ag = db.scalar(select(Agendamento).where(Agendamento.conector == "sil"))
        assert ag is not None and ag.ativo is False
        r = client.post("/api/agenda/sil/executar-agora", headers=tokens["admin"])
        assert r.status_code == 409, r.text
        db.expire_all()
        assert db.scalar(select(Agendamento).where(Agendamento.conector == "sil")).forcar is False
        aud = _ultima_aud(db, "agenda_executar_agora_recusada")
        assert aud.alvo == "agendamento:sil" and aud.detalhe["motivo"] == "desligado"
        assert sil_mock.hits == []
    finally:
        get_settings.cache_clear()


# ---------------------------------------------------------------- QA #25 ciclo 1

def test_risco_teto_1_lote_marca_carga_completa_como_concluida(db, sil_mock):
    """ACH-1 (QA #25 @18f8fb1): 150 empresas na API, teto 1. A carga NÃO pode virar "concluída": fica parcial, o
    checkpoint não fecha e a 2ª execução continua do CCM 51."""
    from app.models.controle import CheckpointCarga
    _zera_checkpoint_sil(db)
    cc.executar_amostra(db, "admin@aion.local", "sil")
    ex1 = ccomp.iniciar(db, "admin@aion.local", "sil")
    db.expire_all()
    ex1 = db.get(Execucao, ex1.id)
    cp = db.scalar(select(CheckpointCarga).where(CheckpointCarga.sistema == "SIL"))
    assert not (ex1.status == "sucesso" and "concluída" in (ex1.mensagem or "")), "teto de 1 lote vira 'concluída'"
    assert ex1.status == "parcial" and "reconciliacao" not in ex1.mensagem
    assert cp.concluida is False and cp.ultima_chave == [50]
    n0 = len(sil_mock.gets())
    ex2 = ccomp.iniciar(db, "admin@aion.local", "sil")
    db.expire_all()
    seqs = sil_mock.gets()[n0:]
    cp = db.scalar(select(CheckpointCarga).where(CheckpointCarga.sistema == "SIL"))
    assert db.get(Execucao, ex2.id).status == "parcial" and len(seqs) == 1, sil_mock.sequencias
    assert sil_mock.sequencias[-1] == 51 and cp.concluida is False and cp.ultima_chave == [100]
    # 3ª execução: lote 101-150 (cheio) ainda parcial; a 4ª acha o fim (lote vazio) e conclui com reconciliação
    ccomp.iniciar(db, "admin@aion.local", "sil")
    ex4 = ccomp.iniciar(db, "admin@aion.local", "sil")
    db.expire_all()
    ex4 = db.get(Execucao, ex4.id)
    assert ex4.status == "sucesso" and "reconciliacao=" in ex4.mensagem, ex4.mensagem
    assert sil_mock.sequencias[-1] == 151
    _zera_checkpoint_sil(db)


@pytest.mark.parametrize("erro", [httpx.ReadTimeout("zz"), httpx.ConnectError("zz")])
def test_r1_retentativa_de_get_loga_info_sem_warning(caplog, erro):
    """R1 trava: timeout/erro de conexão de cada tentativa de GET é INFO; sem queda de ambiente, nenhum WARNING."""
    falhas = {"n": 2}
    api = Api()

    def transporte(req):
        if req.method == "GET" and falhas["n"]:
            falhas["n"] -= 1
            raise erro
        return api(req)
    con = SilConnector(_cfg(), transporte=httpx.MockTransport(transporte), esperar=lambda s: None, amostra_limite=1000)
    with caplog.at_level(logging.DEBUG, logger="sigpmf.conector.sil"):
        assert len(con.ler_amostra("Empresas")) == 50
    tentativas = [r for r in caplog.records if "(tentativa" in r.getMessage()
                  and ("timeout" in r.getMessage() or "erro de conexão" in r.getMessage())]
    assert len(tentativas) == 2 and all(r.levelno == logging.INFO for r in tentativas)
    assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []
