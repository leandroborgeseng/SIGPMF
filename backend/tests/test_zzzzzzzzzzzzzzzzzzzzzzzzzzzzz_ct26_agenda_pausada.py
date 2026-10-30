"""CT26: item da agenda com ativo=false bloqueia a carga completa manual (409, nada criado nem enfileirado).
A amostra continua liberada. Só dados sintéticos (Leitor de teste), banco *_test."""
import pytest
from sqlalchemy import func, select, update

from app.models.controle import Agendamento, Auditoria, Execucao
from app.services import carga_completa as ccomp
from app.services import carga_conector as cc
from tests.conftest import TEST_URL
from tests.test_zzz_carga_completa import _amostra, origem  # noqa: F401 - fixture reutilizada

pytestmark = pytest.mark.agenda_real
URL = "/api/conectores/cemiterio/completa"


@pytest.fixture
def agenda(db):
    assert "_test" in TEST_URL.rsplit("/", 1)[-1].split("?")[0]
    antes = db.scalar(select(Agendamento.ativo).where(Agendamento.conector == "cemiterio"))
    assert antes is not None, "a migração semeia a agenda do cemitério"

    def define(valor):
        db.execute(update(Agendamento).where(Agendamento.conector == "cemiterio").values(ativo=valor, forcar=False))
        db.commit()
    yield define
    define(antes)


def _completas(db):
    db.expire_all()
    return db.scalar(select(func.count()).select_from(Execucao).where(Execucao.tipo == ccomp.TIPO))


def test_ct26_pausada_responde_409_e_nao_cria_nem_enfileira(client, tokens, origem, db, agenda, monkeypatch):  # noqa: F811
    _amostra(db, "cemiterio")
    agenda(False)
    disparos = []
    monkeypatch.setattr(ccomp, "disparar", lambda *a, **k: disparos.append(a))
    antes = _completas(db)
    r = client.post(URL, headers=tokens["admin"])
    assert r.status_code == 409
    assert r.json()["detail"] == "Fonte pausada na agenda. Reative antes de rodar a carga completa."
    assert _completas(db) == antes and disparos == []
    aud = db.scalar(select(Auditoria).where(Auditoria.acao == "carga_completa_negada").order_by(Auditoria.id.desc()))
    assert aud.detalhe == {"motivo": "agenda_pausada"}
    # card: mesmo motivo e botão desabilitado (liberada=false)
    s = client.get(URL, headers=tokens["admin"]).json()
    assert s["liberada"] is False and s["motivo"] == ccomp.PAUSADA_NA_AGENDA


def test_ct26_pausada_amostra_continua_liberada(client, tokens, origem, db, agenda):  # noqa: F811
    agenda(False)
    sis = cc.CONECTORES["cemiterio"][0]
    from tests.test_zz_carga_conector import Leitor
    ex = cc.executar_amostra(db, "admin@aion.local", "cemiterio", leitor=Leitor(sis))
    assert ex.status in ("sucesso", "sem_novidades")


def test_ct26_ativa_funciona_como_antes(client, tokens, origem, db, agenda):  # noqa: F811
    _amostra(db, "cemiterio")
    agenda(True)
    antes = _completas(db)
    r = client.post(URL, headers=tokens["admin"])
    assert r.status_code == 202, r.text
    assert _completas(db) == antes + 1
    assert client.get(URL, headers=tokens["admin"]).json()["motivo"] is None


def test_ct26_sem_item_na_agenda_nao_bloqueia(db):
    assert ccomp.motivo_pausada_agenda(db, "conector_sem_agenda_zzteste") is None
