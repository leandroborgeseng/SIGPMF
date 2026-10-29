"""Agenda da SIL bloqueada às 04h (DevOps 29/10): a amostra da tela grava tipo "enriquecimento_sil" e a carga
completa só aceitava "amostra_conector"/"carga_municipe_amostra". E investigação do checkpoint parado em 49.
Só SIL simulada (httpx.MockTransport, hosts .invalid) e dados ZZTESTE; nenhuma rede real."""
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import httpx
import pytest
from sqlalchemy import select, update

from app.bronze.ingestao import ingere
from app.core import recursos
from app.models.controle import Agendamento, Execucao
from app.services import agenda
from app.services import carga_completa as ccomp
from app.services import carga_conector as cc
from tests.test_zzzzzzzzzzzzzzzz_sil_unificado import SANDBOX, Api, _checkpoint_sil, _con, _zera_checkpoint_sil, sil_mock  # noqa: F401, F811

SP = ZoneInfo("America/Sao_Paulo")
CRIADAS: list[int] = []


def _exec(db, tipo, sistema, status):
    ex = Execucao(tipo=tipo, sistema=sistema, status=status, usuario="zz@aion.local",
                  finalizado_em=datetime.now(UTC))
    db.add(ex)
    db.commit()
    CRIADAS.append(ex.id)
    return ex


def _bronze_sil(db):
    ex = _exec(db, "enriquecimento_sil", "SIL", "em_andamento")
    ingere(db, "SIL", "empresas", "_id_sigpmf", None,
           [{"_id_sigpmf": "zz-agenda-1", "endpoint": "empresas_lote", "resposta": {"Nome": "ZZTESTE"}}], ex.id)
    db.commit()


@pytest.fixture(autouse=True)
def _limpa(db):
    yield
    if CRIADAS:  # tira as execuções sintéticas do caminho dos próximos testes
        db.execute(update(Execucao).where(Execucao.id.in_(CRIADAS)).values(status="interrompida"))
        db.commit()
        CRIADAS.clear()


def test_amostra_enriquecimento_sil_com_sucesso_libera_a_completa(client, tokens, db, monkeypatch):
    monkeypatch.setattr(recursos, "disco", lambda: recursos.Uso(total=100, livre=50))
    _bronze_sil(db)
    _exec(db, "enriquecimento_sil", "SIL", "falha")
    assert ccomp.motivo_bloqueio(db, "sil") == ccomp.SEM_AMOSTRA
    _exec(db, "enriquecimento_sil", "SIL", "sucesso")
    assert ccomp.motivo_bloqueio(db, "sil") is None
    r = client.get("/api/conectores/sil/completa", headers=tokens["admin"])
    assert r.status_code == 200 and r.json()["liberada"] is True, r.text


def test_amostra_enriquecimento_sil_com_falha_continua_bloqueando(client, tokens, db):
    _bronze_sil(db)
    _exec(db, "enriquecimento_sil", "SIL", "sucesso")
    _exec(db, "enriquecimento_sil", "SIL", "falha")
    assert ccomp.motivo_bloqueio(db, "sil") == ccomp.SEM_AMOSTRA
    r = client.get("/api/conectores/sil/completa", headers=tokens["admin"])
    assert r.json()["liberada"] is False


def test_enriquecimento_sil_nao_libera_outro_conector(db):
    _exec(db, "amostra_conector", "SAUDE", "falha")
    _exec(db, "enriquecimento_sil", "SAUDE", "sucesso")  # mesmo com o tipo, fora da SIL não vale
    assert ccomp.motivo_bloqueio(db, "saude") == ccomp.SEM_AMOSTRA
    assert "enriquecimento_sil" not in ccomp.TIPOS_AMOSTRA_EXTRA.get("saude", ())


def test_ciclo_das_04h_inicia_a_sil_depois_da_amostra_da_tela(db, sil_mock, monkeypatch):  # noqa: F811
    monkeypatch.setenv("AGENDA_JANELA", "00:00-06:00")
    from app.core.config import get_settings
    get_settings.cache_clear()
    _bronze_sil(db)
    _exec(db, "enriquecimento_sil", "SIL", "sucesso")
    _zera_checkpoint_sil(db)
    db.execute(update(Agendamento).values(ativo=False, forcar=False))
    db.execute(update(Agendamento).where(Agendamento.conector == "sil").values(
        ativo=True, horario="04:00", ultima_data=None, tentativas=0, proxima_tentativa_em=None))
    db.commit()
    antes = db.scalar(select(Execucao.id).order_by(Execucao.id.desc()).limit(1))
    try:
        agora = datetime(2026, 10, 30, 4, 5, tzinfo=SP).astimezone(UTC)
        feitos = dict(agenda.ciclo(db, agora=agora, leitores={"sil": _con(sil_mock)}))
        assert feitos.get("sil") in ("sucesso", "parcial"), feitos
        ex = db.scalar(select(Execucao).where(Execucao.id > antes, Execucao.sistema == "SIL",
                                              Execucao.usuario == agenda.USUARIO))
        assert ex is not None                                         # criou a execução (antes: só "bloqueada")
        assert sil_mock.gets() and {h[0] for h in sil_mock.hits} == {SANDBOX}
    finally:
        db.execute(update(Agendamento).where(Agendamento.conector == "sil").values(ativo=False, ultima_data=None))
        db.commit()
        _zera_checkpoint_sil(db)


# ---------------------------------------------------------------- checkpoint parado em 49 (só mock)

class Captura(Api):
    """Guarda as requisições de dados exatamente como saem do ClienteSil."""
    def __init__(self, ignora_sequencia=False, **kw):
        super().__init__(**kw)
        self.reqs: list[httpx.Request] = []
        self.ignora = ignora_sequencia

    def __call__(self, req):
        if req.url.path.endswith("consultarlote"):
            self.reqs.append(req)
            if self.ignora:  # SIL que ignora o parâmetro: sempre a mesma página (CCM 1..49)
                self.sequencias.append(int(req.url.params["Sequencia"]))
                self.hits.append((req.url.host, req.method, req.url.path))
                return httpx.Response(200, json={"empresas": [{"empresa": {"Ccm": str(c), "Nome": f"ZZTESTE {c}"}}
                                                              for c in range(1, 50)]})
        return super().__call__(req)


def _ler(api, ultima=None, max_lotes=5):
    con = _con(api, max_lotes=max_lotes)
    s = con.abrir()
    try:
        return con.ler_pagina(s, "Empresas", None, ("Ccm",), ultima, 5000), s.parcial()
    finally:
        s.close()


def test_requisicao_montada_bate_com_a_doc():
    """Doc (sil/spec-franca.rtf): GET /franca/empresa/consultarlote, `Sequencia` na query, obrigatório, string."""
    api = Captura(ccms=range(1, 121))
    _ler(api, ultima=(49,))
    r = api.reqs[0]
    assert r.method == "GET" and r.url.path == "/v1/franca/empresa/consultarlote"
    assert dict(r.url.params) == {"Sequencia": "50"}                  # nome exato, na query, CCM+1
    assert r.content == b"" and r.headers["Authorization"].startswith("Bearer ")
    assert [int(x.url.params["Sequencia"]) for x in api.reqs] == [50, 100, 121]  # keyset: último CCM + 1


def test_api_que_ignora_sequencia_para_parcial_sem_avancar():
    linhas, parcial = _ler(Captura(ignora_sequencia=True), ultima=(49,))
    assert linhas == []                                               # nada abaixo da Sequencia é gravado
    assert parcial["alerta"]["motivo"] == "sem_ccm_novo" and parcial["alerta"]["sequencia"] == 50


@pytest.mark.usefixtures("sil_mock")
def test_carga_completa_com_api_ignorando_sequencia_fica_parcial_e_checkpoint_em_49(db, monkeypatch):
    api = Api(ccms=range(1, 50))
    monkeypatch.setattr(cc, "leitor_de", lambda conector, cfg: _con(api))
    a = cc.executar_amostra(db, "admin@aion.local", "sil")
    assert a.status in ("sucesso", "sem_novidades"), a.mensagem
    _zera_checkpoint_sil(db)
    api2 = Captura(ignora_sequencia=True)
    monkeypatch.setattr(cc, "leitor_de", lambda conector, cfg: _con(api2, max_lotes=5))
    ex = ccomp.iniciar(db, "admin@aion.local", "sil")
    db.expire_all()
    ex = db.get(Execucao, ex.id)
    cp = _checkpoint_sil(db)
    try:
        # 1ª página 1..49 (não cheia) termina a leitura; checkpoint no último CCM gravado, sem pular nada
        assert cp.ultima_chave == [49], cp.ultima_chave
        assert [int(x.url.params["Sequencia"]) for x in api2.reqs][0] == 1
    finally:
        _zera_checkpoint_sil(db)


# ---------------------------------------------------------------- TL 29/10: base oficial padrão, sandbox só explícito

def _env_sil(monkeypatch, sandbox_flag=None):
    from tests.test_zzzzzzzzzzzzzzzz_sil_unificado import PROD
    monkeypatch.setenv("SIL_BASE_URL", f"https://{PROD}/v1")
    monkeypatch.setenv("SIL_BASE_URL_SANDBOX", f"https://{SANDBOX}/v1")
    monkeypatch.setenv("SIL_CLIENT_ID", "zz-id")
    monkeypatch.setenv("SIL_CLIENT_SECRET", "zz-segredo-sintetico")
    if sandbox_flag is None:
        monkeypatch.delenv("SIL_USAR_SANDBOX", raising=False)
    else:
        monkeypatch.setenv("SIL_USAR_SANDBOX", sandbox_flag)


def test_padrao_usa_a_oficial_mesmo_com_sandbox_configurado(monkeypatch):
    from app.connectors.sil.config import ConfigSil
    from tests.test_zzzzzzzzzzzzzzzz_sil_unificado import PROD
    _env_sil(monkeypatch)
    c = ConfigSil.do_ambiente()
    assert c.bases() == [("producao", f"https://{PROD}/v1")] and c.ambiente == "producao" and c.host == PROD
    api = Api()
    from app.connectors.sil.conector import SilConnector
    SilConnector(c, transporte=httpx.MockTransport(api), esperar=lambda s: None).ler_amostra("Empresas")
    assert {h[0] for h in api.hits} == {PROD}                      # nunca toca a homologação


@pytest.mark.parametrize("flag", ["false", "0", "", "nao"])
def test_flag_desligada_nao_usa_sandbox(monkeypatch, flag):
    from app.connectors.sil.config import ConfigSil
    _env_sil(monkeypatch, flag)
    assert ConfigSil.do_ambiente().ambiente == "producao"


def test_sandbox_so_com_flag_explicita_e_sem_fallback(monkeypatch):
    from app.connectors.sil.conector import ErroSil, SilConnector
    from app.connectors.sil.config import ConfigSil
    _env_sil(monkeypatch, "true")
    c = ConfigSil.do_ambiente()
    assert c.bases() == [("sandbox", f"https://{SANDBOX}/v1")]   # única base: a oficial não é tentada
    api = Api(rede_sandbox=True)
    with pytest.raises(ErroSil):
        SilConnector(c, transporte=httpx.MockTransport(api), esperar=lambda s: None).ler_amostra("Empresas")
    assert {h[0] for h in api.hits} == {SANDBOX}


def test_oficial_fora_da_rede_nao_cai_para_sandbox():
    from app.connectors.sil.conector import ErroSil
    from tests.test_zzzzzzzzzzzzzzzz_sil_unificado import PROD

    class ApiRedeProd(Api):
        def __call__(self, req):
            if req.url.host == PROD:
                self.hits.append((req.url.host, req.method, req.url.path))
                raise httpx.ConnectError("zz sem rede")
            return super().__call__(req)
    api = ApiRedeProd()
    with pytest.raises(ErroSil):
        _con(api, sandbox=False).ler_amostra("Empresas")
    from app.connectors.sil.config import ConfigSil
    con = ConfigSil(f"https://{PROD}/v1", "zz-id", "zz-s", "zz", base_url_sandbox=f"https://{SANDBOX}/v1")
    from app.connectors.sil.conector import SilConnector
    with pytest.raises(ErroSil):
        SilConnector(con, transporte=httpx.MockTransport(api), esperar=lambda s: None).ler_amostra("Empresas")
    assert {h[0] for h in api.hits} == {PROD}


def test_execucao_registra_a_base_usada_no_log(db, sil_mock, monkeypatch):  # noqa: F811
    from app.models.controle import Log
    from tests.test_zzzzzzzzzzzzzzzz_sil_unificado import PROD
    _env_sil(monkeypatch)
    a = cc.executar_amostra(db, "admin@aion.local", "sil")
    msgs = [m for (m,) in db.execute(select(Log.mensagem).where(Log.execucao_id == a.id))]
    assert any(f"ambiente=producao host={PROD}" in m for m in msgs), msgs
    assert not any("zz-segredo" in m or "zz-id" in m for m in msgs)


# ---------------------------------------------------------------- 0019/0020: ambiente_origem na bronze

def _limpa_sil(db):
    from app.models.camadas import RegistroBronze
    db.query(RegistroBronze).filter(RegistroBronze.sistema_origem == "SIL").delete()
    db.commit()


def test_ingere_marca_o_ambiente_e_prata_so_le_producao(db, monkeypatch):
    from app.models.camadas import EmpresaSil, RegistroBronze
    from app.silver import tributario
    _limpa_sil(db)
    ex = _exec(db, "amostra_conector", "SIL", "sucesso")
    _env_sil(monkeypatch, "true")
    ingere(db, "SIL", "Empresas", "Ccm", None, [{"Ccm": 9001, "Nome": "ZZTESTE SANDBOX"},
                                                 {"Ccm": 9002, "Nome": "ZZTESTE IGUAL"}], ex.id)
    _env_sil(monkeypatch)
    c = ingere(db, "SIL", "Empresas", "Ccm", None, [{"Ccm": 9002, "Nome": "ZZTESTE IGUAL"},
                                                     {"Ccm": 9003, "Nome": "ZZTESTE PROD"}], ex.id)
    assert c["ignorados"] == 0                                     # a mesma linha da produção não colide
    amb = set(db.scalars(select(RegistroBronze.id_origem)
                         .where(RegistroBronze.sistema_origem == "SIL", RegistroBronze.ambiente_origem == "sandbox")))
    assert amb == {"9001", "9002"}
    tributario.reconstruir(db)
    db.flush()
    assert sorted(db.scalars(select(EmpresaSil.ccm))) == [9002, 9003]
    db.rollback()
    _limpa_sil(db)


def test_outros_sistemas_sempre_producao(db, monkeypatch):
    from app.bronze.ingestao import ambiente_da_origem
    _env_sil(monkeypatch, "true")
    assert ambiente_da_origem("SIL") == "sandbox" and ambiente_da_origem("SAUDE") == "producao"


def _cenario(db, novos=(1, 1)):
    from app.models.camadas import RegistroBronze
    _limpa_sil(db)
    exs = []
    for i, n in enumerate(novos):
        ex = _exec(db, "enriquecimento_sil", "SIL", "sucesso")
        ex.novos = n
        db.commit()
        for k in range(n):
            db.add(RegistroBronze(sistema_origem="SIL", tabela="empresas", id_origem=f"zz-{i}-{k}",
                                  dados={"Nome": "ZZTESTE"}, hash=f"{i:02d}{k:062d}", execucao_id=ex.id))
        exs.append(ex.id)
    db.commit()
    return tuple(exs)


def _ambientes(db, ids):
    from app.models.camadas import RegistroBronze
    db.expire_all()
    return sorted(db.scalars(select(RegistroBronze.ambiente_origem).where(RegistroBronze.execucao_id.in_(ids))))


def test_0020_marca_so_as_execucoes_e_e_idempotente(db):
    from app.services.marca_sandbox_sil import marcar
    ids = _cenario(db)
    r = marcar(db.connection(), ids)
    db.commit()
    assert r == {"esperado": 2, "encontradas": 2, "marcadas": 2}
    assert _ambientes(db, ids) == ["sandbox", "sandbox"]
    r2 = marcar(db.connection(), ids)                              # 2ª vez: nada muda
    db.commit()
    assert r2["marcadas"] == 0 and _ambientes(db, ids) == ["sandbox", "sandbox"]
    _limpa_sil(db)


def test_0020_nao_toca_outras_execucoes(db):
    from app.services.marca_sandbox_sil import marcar
    ids = _cenario(db, (1, 1, 1))
    marcar(db.connection(), ids[:2])
    db.commit()
    assert _ambientes(db, ids[2:]) == ["producao"]
    _limpa_sil(db)


def test_0020_contagem_divergente_aborta_sem_mudar(db):
    from app.models.controle import Execucao as Ex
    from app.services.marca_sandbox_sil import ContagemDivergente, marcar
    ids = _cenario(db)
    db.get(Ex, ids[0]).novos = 5                                   # metadado diz 5+1, bronze tem 2
    db.commit()
    with pytest.raises(ContagemDivergente):
        marcar(db.connection(), ids)
    db.rollback()
    assert _ambientes(db, ids) == ["producao", "producao"]
    _limpa_sil(db)


def test_0020_acima_do_teto_aborta(db):
    from app.services.marca_sandbox_sil import ContagemDivergente, marcar
    ids = _cenario(db, (3,))
    with pytest.raises(ContagemDivergente):
        marcar(db.connection(), ids, teto=2)
    db.rollback()
    assert _ambientes(db, ids) == ["producao"] * 3
    _limpa_sil(db)


def test_0020_banco_sem_as_execucoes_nao_faz_nada(db):
    from app.services.marca_sandbox_sil import marcar
    assert marcar(db.connection(), (987654, 987655))["marcadas"] == 0
    db.rollback()
