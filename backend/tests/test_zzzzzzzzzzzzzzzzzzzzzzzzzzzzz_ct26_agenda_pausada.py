"""CT26 v2 (a-d): agenda com ativo=false -> 409 na carga completa e em toda rota de amostra (pedir, aprovar, rodar),
sem execução criada e sem mudar `forcar`. Com ativo=true nada muda; aprovar a amostra com forcar=false não dispara
a carga completa (CT26d). Só dados sintéticos ZZTESTE, banco *_test. Nunca chama POST /franca/imoveis/atualizar."""
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select, update

from app.models.controle import Agendamento, Auditoria, Execucao
from app.services import amostra_sil
from app.services import carga_completa as ccomp
from app.services import carga_conector as cc
from tests.conftest import TEST_URL
from tests.test_zz_carga_conector import Leitor
from tests.test_zzz_carga_completa import _amostra, origem  # noqa: F401 - fixture reutilizada

pytestmark = pytest.mark.agenda_real
MSG_COMPLETA = "Fonte pausada na agenda. Reative antes de rodar a carga completa."
MSG_AMOSTRA = "Fonte pausada na agenda. Reative antes de rodar a amostra."


@pytest.fixture
def agenda(db):
    assert "_test" in TEST_URL.rsplit("/", 1)[-1].split("?")[0]
    antes = {c: (a, f) for c, a, f in db.execute(select(Agendamento.conector, Agendamento.ativo, Agendamento.forcar)
                                                  .where(Agendamento.conector.in_(("cemiterio", "sil"))))}
    assert set(antes) == {"cemiterio", "sil"}, "a migração semeia a agenda"

    def define(conector, ativo, forcar=False):
        db.execute(update(Agendamento).where(Agendamento.conector == conector).values(ativo=ativo, forcar=forcar))
        db.commit()
    yield define
    for c, (a, f) in antes.items():
        define(c, a, f)


def _execs(db):
    db.expire_all()
    return db.scalar(select(func.count()).select_from(Execucao))


def _forcar(db, conector):
    db.expire_all()
    return db.scalar(select(Agendamento.forcar).where(Agendamento.conector == conector))


def _ultima_aud(db, acao):
    db.expire_all()
    return db.scalar(select(Auditoria).where(Auditoria.acao == acao).order_by(Auditoria.id.desc()))


def _amostra_manual(db, conector):
    """Amostra sintética concluída e marcada como manual (aprovável)."""
    if conector == "sil":
        ex = Execucao(tipo="enriquecimento_sil", sistema="SIL", status="sucesso", usuario="admin@aion.local",
                      finalizado_em=datetime.now(UTC), mensagem="ZZTESTE")
        db.add(ex)
        db.commit()
        acao = "amostra_sil_iniciada"
    else:
        ex = cc.executar_amostra(db, "admin@aion.local", conector, leitor=Leitor(cc.CONECTORES[conector][0]))
        acao = "amostra_conector_iniciada"
    db.add(Auditoria(usuario="admin@aion.local", acao=acao, alvo=f"execucao:{ex.id}",
                     detalhe={"conector": conector, "aprovacao": "manual"}))
    db.commit()
    return ex


def _pausada_409(client, tokens, db, agenda, conector, metodo_url, msg, evento):
    agenda(conector, False, forcar=True)
    antes = _execs(db)
    r = client.post(metodo_url, headers=tokens["admin"])
    assert r.status_code == 409 and r.json()["detail"] == msg, r.text
    assert _execs(db) == antes  # nenhuma execução criada
    assert _forcar(db, conector) is True  # forcar não muda
    assert _ultima_aud(db, evento).detalhe == {"motivo": "agenda_pausada"}
    assert "ZZTESTE" not in r.text and "Traceback" not in r.text


# ---------- CT26a: uma rota por teste, pausada -> 409 ----------
def test_ct26a_completa_pausada(client, tokens, origem, db, agenda, monkeypatch):  # noqa: F811
    _amostra(db, "cemiterio")
    disparos = []
    monkeypatch.setattr(ccomp, "disparar", lambda *a, **k: disparos.append(a))
    _pausada_409(client, tokens, db, agenda, "cemiterio", "/api/conectores/cemiterio/completa", MSG_COMPLETA,
                 "carga_completa_negada")
    assert disparos == []
    s = client.get("/api/conectores/cemiterio/completa", headers=tokens["admin"]).json()  # CT26c: card bloqueado
    assert s["liberada"] is False and s["motivo"] == MSG_COMPLETA


def test_ct26a_retomar_camadas_pausada(client, tokens, origem, db, agenda):  # noqa: F811
    _pausada_409(client, tokens, db, agenda, "cemiterio", "/api/conectores/cemiterio/completa/retomar-camadas",
                 MSG_COMPLETA, "carga_completa_negada")


def test_ct26a_amostra_generica_pausada(client, tokens, db, agenda, monkeypatch):
    chamadas = []
    monkeypatch.setattr(cc, "iniciar_amostra", lambda *a, **k: chamadas.append(a))
    _pausada_409(client, tokens, db, agenda, "cemiterio", "/api/conectores/cemiterio/amostra", MSG_AMOSTRA,
                 "amostra_conector_recusada")
    assert chamadas == []


def test_ct26a_amostra_sil_pausada(client, tokens, db, agenda, monkeypatch):
    chamadas = []
    monkeypatch.setattr(amostra_sil, "iniciar_amostra", lambda *a, **k: chamadas.append(a))
    _pausada_409(client, tokens, db, agenda, "sil", "/api/conectores/sil/amostra", MSG_AMOSTRA, "amostra_sil_recusada")
    assert chamadas == []


@pytest.mark.parametrize("conector", ["cemiterio", "sil"])
def test_ct26a_aprovar_amostra_pausada(client, tokens, db, agenda, conector):
    ex = _amostra_manual(db, conector)
    _pausada_409(client, tokens, db, agenda, conector, f"/api/conectores/{conector}/amostra/{ex.id}/aprovar",
                 MSG_AMOSTRA, "amostra_aprovacao_recusada")


# ---------- CT26b: ativa -> como antes ----------
def test_ct26b_completa_ativa(client, tokens, origem, db, agenda):  # noqa: F811
    _amostra(db, "cemiterio")
    agenda("cemiterio", True)
    antes = _execs(db)
    r = client.post("/api/conectores/cemiterio/completa", headers=tokens["admin"])
    assert r.status_code == 202, r.text
    assert _execs(db) == antes + 1
    assert client.get("/api/conectores/cemiterio/completa", headers=tokens["admin"]).json()["motivo"] is None


def test_ct26b_retomar_camadas_ativa_segue_regra_antiga(client, tokens, origem, db, agenda):  # noqa: F811
    agenda("cemiterio", True)
    r = client.post("/api/conectores/cemiterio/completa/retomar-camadas", headers=tokens["admin"])
    assert r.json()["detail"] != MSG_COMPLETA  # cai nas regras de antes (nada a retomar), não na pausa


def test_ct26b_amostra_generica_ativa(client, tokens, db, agenda, monkeypatch):
    agenda("cemiterio", True)
    monkeypatch.setattr(cc, "leitor_de", lambda conector, cfg: Leitor(cc.CONECTORES["cemiterio"][0]))
    r = client.post("/api/conectores/cemiterio/amostra", headers=tokens["admin"])
    assert r.status_code == 202, r.text


def test_ct26b_amostra_sil_ativa(client, tokens, db, agenda, monkeypatch):
    agenda("sil", True)

    def stub(db_, usuario, *a, **k):
        ex = Execucao(tipo="enriquecimento_sil", sistema="SIL", status="sucesso", usuario=usuario, mensagem="ZZTESTE")
        db_.add(ex)
        db_.commit()
        return ex
    monkeypatch.setattr(amostra_sil, "iniciar_amostra", stub)
    r = client.post("/api/conectores/sil/amostra", headers=tokens["admin"])
    assert r.status_code == 202, r.text


# ---------- CT26d: ativa + forcar=false, aprovar não dispara a completa ----------
@pytest.mark.parametrize("conector", ["cemiterio", "sil"])
def test_ct26d_aprovar_ativa_sem_forcar_nao_dispara_completa(client, tokens, db, agenda, monkeypatch, conector):
    agenda(conector, True, forcar=False)
    ex = _amostra_manual(db, conector)
    disparos = []
    monkeypatch.setattr(ccomp, "disparar", lambda *a, **k: disparos.append(a))
    monkeypatch.setattr(ccomp, "iniciar", lambda *a, **k: disparos.append(a))
    antes = _execs(db)
    r = client.post(f"/api/conectores/{conector}/amostra/{ex.id}/aprovar", headers=tokens["admin"])
    assert r.status_code == 200, r.text
    assert disparos == [] and _execs(db) == antes
    assert _forcar(db, conector) is False


def test_ct26_sem_item_na_agenda_nao_bloqueia(db):
    assert ccomp.motivo_pausada_agenda(db, "conector_sem_agenda_zzteste") is None
    assert ccomp.motivo_pausada_agenda(db, "conector_sem_agenda_zzteste", "amostra") is None
