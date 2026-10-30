"""Observabilidade do SIL (TL 30/10, execução 349 sem detalhe): logger do SIL em stdout, WARNING sem valores no 200
inesperado com uma repetição, e ErroSil com status/tentativa/Sequencia/classe no log e no Log da execução.
Nenhuma rede real: httpx.MockTransport, hosts .invalid, credenciais sintéticas."""
import io
import logging

import httpx
import pytest
from sqlalchemy import select

from app.connectors.sil.cliente import ErroSil, FalhaCredencial, FalhaRede, FalhaSil, contexto_erro_sil
from app.connectors.sil.conector import ConfigSil, SilConnector
from app.core import recursos
from app.core.config import get_settings
from app.models.controle import Execucao, Log
from app.services import carga_completa as ccomp
from app.services import carga_conector as cc

SECRET = "zz-segredo-sintetico-log"
HOST = "sandbox.zzlog.invalid"
TOKEN = "ZZTOKEN-LOG-SECRETO"
CCM_SECRETO = "987654321"


def _cfg():
    return ConfigSil(base_url=f"https://{HOST}/v1", client_id="zz-id", client_secret=SECRET,
                     tentativas=2, backoff_s=0, backoff_max_s=0, req_por_s=1000, timeout_s=1, max_lotes_por_execucao=5)


class Api:
    def __init__(self, respostas):
        self.respostas = list(respostas)  # um item por GET: (status, json) ou exceção
        self.sequencias = []

    def __call__(self, req):
        if req.url.path.endswith("/api/authenticate"):
            return httpx.Response(200, json={"access_token": TOKEN, "expires_in": 600})
        self.sequencias.append(int(req.url.params["Sequencia"]))
        r = self.respostas.pop(0) if self.respostas else (200, {"empresas": []})
        if isinstance(r, Exception):
            raise r
        if r[1] is None:  # corpo literal `null`
            return httpx.Response(r[0], content=b"null", headers={"Content-Type": "application/json"})
        return httpx.Response(r[0], json=r[1])


def _con(api):
    return SilConnector(_cfg(), transporte=httpx.MockTransport(api), esperar=lambda s: None, amostra_limite=1000,
                        max_lotes=5)


def _lote(ccms):
    return {"empresas": [{"empresa": {"Ccm": str(c), "Nome": f"EMPRESA ZZ {c}"}} for c in ccms]}


def _sem_segredo(texto):
    for proibido in (TOKEN, SECRET, CCM_SECRETO, "Sequencia=", HOST, "Bearer"):
        if proibido == "Sequencia=":
            continue
        assert proibido not in texto, proibido


# ------------------------------------------------------------------ (1) logger do SIL chega ao stdout
def test_logger_sil_info_vai_para_stdout(monkeypatch):
    from app import main
    buf = io.StringIO()
    monkeypatch.setattr("sys.stdout", buf)
    for nome in ("sigpmf.conector.sil", "sigpmf.sil", "sigpmf.carga_completa"):  # refaz o handler apontando para o stdout simulado
        lg = logging.getLogger(nome)
        for h in [h for h in lg.handlers if getattr(h, "_sigpmf_stdout", False)]:
            lg.removeHandler(h)
    main.configurar_logs()
    main.configurar_logs()  # idempotente
    try:
        lg = logging.getLogger("sigpmf.conector.sil")
        assert lg.getEffectiveLevel() == logging.INFO and not lg.propagate
        assert sum(getattr(h, "_sigpmf_stdout", False) for h in lg.handlers) == 1
        lg.info("SIL GET /franca/empresa/consultarlote -> 200 em 5 ms (tentativa 1)")
        logging.getLogger("sigpmf.sil").info("SIL: zz info")
        logging.getLogger("sigpmf.carga_completa").warning("zz fechamento ErroSil")
        out = buf.getvalue()
        assert "WARNING sigpmf.carga_completa zz fechamento ErroSil" in out
        assert "INFO sigpmf.conector.sil SIL GET /franca/empresa/consultarlote -> 200" in out
        assert "INFO sigpmf.sil SIL: zz info" in out
        assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING
        assert logging.getLogger("httpcore").getEffectiveLevel() >= logging.WARNING
    finally:
        for nome in ("sigpmf.conector.sil", "sigpmf.sil", "sigpmf.carga_completa"):
            lg = logging.getLogger(nome)
            for h in [h for h in lg.handlers if getattr(h, "_sigpmf_stdout", False)]:
                lg.removeHandler(h)
        monkeypatch.undo()
        main.configurar_logs()


def test_get_sil_real_loga_info_sem_query_nem_token(caplog):
    api = Api([(200, _lote(range(1, 3)))])
    with caplog.at_level(logging.INFO, logger="sigpmf.conector.sil"):
        _con(api).ler_amostra("Empresas")
    linhas = [r.getMessage() for r in caplog.records if r.name == "sigpmf.conector.sil" and "(tentativa" in r.getMessage()]
    assert any("-> 200" in m for m in linhas)
    for m in linhas:
        assert "?" not in m and TOKEN not in m and HOST not in m


# ------------------------------------------------------------------ (2) contexto do ErroSil
@pytest.mark.parametrize("resposta,classe,status", [
    ((503, {"e": "zz"}), FalhaSil, 503),
    (httpx.ReadTimeout("zz"), FalhaRede, None),
    ((400, {"e": "zz"}), FalhaSil, 400),
])
def test_erro_sil_leva_status_tentativa_sequencia(resposta, classe, status):
    api = Api([resposta] * 5)
    con = _con(api)
    s = con.abrir()
    with pytest.raises(classe) as e:
        con.lote_empresas(s, 77)
    c = contexto_erro_sil(e.value)
    esperado_tent = 1 if status == 400 else 3  # 400 não repete; 5xx/timeout: 1 + tentativas(2)
    assert c == {"classe": classe.__name__, "status": status, "tentativa": esperado_tent, "repeticao": None,
                 "sequencia": 77}


def test_401_duas_vezes_vira_falha_credencial_com_contexto():
    api = Api([(401, {}), (401, {})])
    con = _con(api)
    with pytest.raises(FalhaCredencial) as e:
        con.lote_empresas(con.abrir(), 5)
    assert contexto_erro_sil(e.value) == {"classe": "FalhaCredencial", "status": 401, "tentativa": 1,
                                             "repeticao": None, "sequencia": 5}


# ------------------------------------------------------------------ (3) 200 inesperado: WARNING sem valores + 1 repetição
def test_200_inesperado_repete_uma_vez_e_falha_com_warning_sem_valores(caplog):
    corpo = {"erro": CCM_SECRETO, "detalhe": {"cnpj": "12345678000199"}}
    api = Api([(200, corpo), (200, corpo)])
    con = _con(api)
    with caplog.at_level(logging.INFO, logger="sigpmf.sil"), pytest.raises(ErroSil) as e:
        con.lote_empresas(con.abrir(), 42)
    assert api.sequencias == [42, 42]  # mesma Sequencia, só uma repetição
    assert str(e.value) == "Resposta inesperada da API SIL em empresa/consultarlote."
    assert contexto_erro_sil(e.value) == {"classe": "ErroSil", "status": 200, "tentativa": 1, "repeticao": 2,
                                          "sequencia": 42}
    avisos = [r.getMessage() for r in caplog.records if r.name == "sigpmf.sil" and r.levelno == logging.WARNING]
    assert len(avisos) == 2
    for m in avisos:
        assert "Sequencia=42" in m and "tipo_json=dict" in m and "['detalhe', 'erro']" in m
        assert CCM_SECRETO not in m and "12345678000199" not in m and "cnpj" not in m


def test_200_inesperado_lista_loga_tipo_sem_chaves(caplog):
    api = Api([(200, ["zz", CCM_SECRETO]), (200, ["zz"])])
    con = _con(api)
    with caplog.at_level(logging.WARNING, logger="sigpmf.sil"), pytest.raises(ErroSil):
        con.lote_empresas(con.abrir(), 9)
    avisos = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert all("tipo_json=list" in m and "chaves=[]" in m and CCM_SECRETO not in m for m in avisos)


def test_200_inesperado_que_se_recupera_na_repeticao_segue(caplog):
    api = Api([(200, {"zz": 1}), (200, _lote(range(10, 13)))])
    con = _con(api)
    s = con.abrir()
    with caplog.at_level(logging.WARNING, logger="sigpmf.sil"):
        linhas = con.lote_empresas(s, 10)
    assert [r["Ccm"] for r in linhas] == [10, 11, 12] and api.sequencias == [10, 10]
    assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 1
    assert s.lotes == 1


def test_corpo_vazio_continua_sendo_fim_sem_repeticao():
    api = Api([(200, {})])
    con = _con(api)
    assert con.lote_empresas(con.abrir(), 3) == [] and api.sequencias == [3]


# ------------------------------------------------------------------ (2) ErroSil encerra a carga: WARNING + Log
@pytest.fixture()
def sil_falha(monkeypatch):
    api = Api([(200, {"zz": CCM_SECRETO})] * 2)
    monkeypatch.setenv("CARGA_COMPLETA_LOTE", "50")
    monkeypatch.setenv("CARGA_COMPLETA_PAUSA_MS", "0")
    monkeypatch.setattr(recursos, "disco", lambda: recursos.Uso(total=100, livre=50))
    get_settings.cache_clear()
    monkeypatch.setattr(cc, "leitor_de", lambda conector, cfg: _con(api))
    monkeypatch.setattr(ccomp, "disparar", lambda ex_id, conector, trava, somente_camadas=False:
                        ccomp.rodar(ex_id, conector, trava, somente_camadas=somente_camadas))
    yield api
    get_settings.cache_clear()


def test_carga_que_falha_por_erro_sil_grava_detalhe_no_log_da_execucao(db, sil_falha, caplog):
    with caplog.at_level(logging.WARNING, logger="sigpmf.carga_completa"):
        ex = Execucao(tipo="carga_completa", sistema="SIL", status="em_andamento", usuario="admin@aion.local")
        db.add(ex)
        db.commit()
        ccomp.rodar(ex.id, "sil", None, leitor=_con(sil_falha), db=db)
    db.expire_all()
    ex = db.get(Execucao, ex.id)
    assert ex.status == "falha", ex.mensagem
    logs = db.scalars(select(Log).where(Log.execucao_id == ex.id)).all()
    det = [lg for lg in logs if lg.mensagem.startswith("Detalhe do erro da API SIL:")]
    assert len(det) == 1 and det[0].nivel == "WARN"
    m = det[0].mensagem
    assert "classe=ErroSil" in m and "status=200" in m and "tentativa=1" in m and "repeticao=2" in m and "sequencia=" in m
    for lg in logs:
        assert CCM_SECRETO not in lg.mensagem and TOKEN not in lg.mensagem and SECRET not in lg.mensagem
    avisos = [r.getMessage() for r in caplog.records
              if r.name == "sigpmf.carga_completa" and "ErroSil" in r.getMessage()]
    assert len(avisos) == 1 and "status=200" in avisos[0] and CCM_SECRETO not in avisos[0]


def test_falha_que_nao_e_erro_sil_nao_grava_detalhe(db, monkeypatch):
    ex = Execucao(tipo="carga_completa", sistema="SIL", status="em_andamento", usuario="admin@aion.local")
    db.add(ex)
    db.commit()
    ccomp._registra_erro_sil(db, ex, RuntimeError("zz"))
    db.commit()
    assert not db.scalars(select(Log).where(Log.execucao_id == ex.id,
                                           Log.mensagem.like("Detalhe do erro da API SIL%"))).all()


def test_null_duas_vezes_vira_erro_sil_e_nao_fim(caplog):
    api = Api([(200, None), (200, None)])
    con = _con(api)
    with caplog.at_level(logging.WARNING, logger="sigpmf.sil"), pytest.raises(ErroSil) as e:
        con.lote_empresas(con.abrir(), 7)
    assert api.sequencias == [7, 7]
    assert contexto_erro_sil(e.value)["repeticao"] == 2
    assert len([r for r in caplog.records if "tipo_json=NoneType" in r.getMessage()]) == 2


def test_404_continua_sendo_fim_da_sequencia():
    api = Api([(404, {"e": "zz"})])
    con = _con(api)
    assert con.lote_empresas(con.abrir(), 7) == [] and api.sequencias == [7]


class ApiTexto(Api):
    def __call__(self, req):
        if req.url.path.endswith("/api/authenticate"):
            return httpx.Response(200, json={"access_token": TOKEN, "expires_in": 600})
        self.sequencias.append(int(req.url.params["Sequencia"]))
        return httpx.Response(200, content=self.respostas.pop(0), headers={"Content-Type": "text/html"})


@pytest.mark.parametrize("corpos", [[b"", b""], [b"<html>zz " + CCM_SECRETO.encode() + b"</html>", b"<html/>"]])
def test_200_nao_json_repete_uma_vez_e_falha_com_warning(caplog, corpos):
    api = ApiTexto(corpos)
    con = _con(api)
    with caplog.at_level(logging.WARNING, logger="sigpmf.sil"), pytest.raises(ErroSil) as e:
        con.lote_empresas(con.abrir(), 8)
    assert api.sequencias == [8, 8]
    assert contexto_erro_sil(e.value) == {"classe": "ErroSil", "status": 200, "tentativa": 1, "repeticao": 2,
                                          "sequencia": 8}
    avisos = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert len(avisos) == 2 and all("tipo_json=nao_json" in m and CCM_SECRETO not in m for m in avisos)
