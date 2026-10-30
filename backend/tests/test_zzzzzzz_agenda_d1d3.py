"""#58 follow-up: D1 (PUT /api/agenda com extra=forbid e forcar StrictBool) e D3 (auditoria grava o "antes" real,
forcar incluso). Só dados sintéticos, banco *_test."""
import pytest
from sqlalchemy import select, update

from app.models.controle import Agendamento, Auditoria
from tests.conftest import TEST_URL
from tests.test_zzzzzzz_agenda import _zera_agenda

URL = "/api/agenda/saude"


@pytest.fixture
def saude(db):
    assert "_test" in TEST_URL.rsplit("/", 1)[-1].split("?")[0]
    _zera_agenda(db, ativo=False)

    def define(**v):
        db.execute(update(Agendamento).where(Agendamento.conector == "saude").values(**v))
        db.commit()
    define(horario="01:00", ordem=1)
    yield define
    _zera_agenda(db, ativo=False)
    define(horario="01:00", ordem=1, forcar=False)


def _linha(db):
    db.expire_all()
    a = db.scalar(select(Agendamento).where(Agendamento.conector == "saude"))
    return a.horario, a.ativo, a.ordem, a.forcar


def _ultima(db, acao):
    db.expire_all()
    return db.scalar(select(Auditoria).where(Auditoria.acao == acao, Auditoria.alvo == "agendamento:saude")
                     .order_by(Auditoria.id.desc()))


# ---------- D1 ----------
@pytest.mark.parametrize("corpo", [
    {"ativo": True, "campo_zzteste": 1},       # campo desconhecido
    {"forcar": "false"}, {"forcar": "true"},  # string
    {"forcar": 0}, {"forcar": 1},              # número
    {"forcar": None, "extra": None},
])
def test_d1_put_agenda_recusa_extra_e_forcar_nao_bool(client, tokens, db, saude, corpo):
    saude(forcar=True, ativo=True)
    antes, n_aud = _linha(db), _ultima(db, "agenda_alterada")
    r = client.put(URL, json=corpo, headers=tokens["admin"])
    assert r.status_code == 422, r.text
    assert _linha(db) == antes  # nada mudou
    assert _ultima(db, "agenda_alterada") == n_aud


def test_d1_forcar_false_bool_continua_valendo(client, tokens, db, saude):
    saude(forcar=True, ativo=True)
    assert client.put(URL, json={"forcar": False}, headers=tokens["admin"]).status_code == 200
    assert _linha(db)[3] is False
    assert client.put(URL, json={"forcar": True}, headers=tokens["admin"]).status_code == 422  # regra antiga


# ---------- D3 ----------
def test_d3_auditoria_grava_antes_real_com_forcar(client, tokens, db, saude):
    saude(ativo=True, forcar=True, horario="03:15", ordem=4)
    r = client.put(URL, json={"ativo": False, "horario": "02:00", "ordem": 2}, headers=tokens["admin"])
    assert r.status_code == 200, r.text
    d = _ultima(db, "agenda_alterada").detalhe
    assert d["antes"] == {"horario": "03:15", "ativo": True, "ordem": 4, "forcar": True}
    assert d["depois"] == {"horario": "02:00", "ativo": False, "ordem": 2, "forcar": False}


def test_d3_limpar_forcar_audita_valor_anterior_real(client, tokens, db, saude):
    saude(ativo=True, forcar=True)
    assert client.put(URL, json={"forcar": False}, headers=tokens["admin"]).status_code == 200
    d = _ultima(db, "agenda_forcar_limpo").detalhe
    assert d["antes"] == {"forcar": True} and d["depois"] == {"forcar": False}
    assert client.put(URL, json={"forcar": False}, headers=tokens["admin"]).status_code == 200  # idempotente
    assert _ultima(db, "agenda_forcar_limpo").detalhe["antes"] == {"forcar": False}


def test_d3_ordem_e_horario_sem_forcar_mantem_forcar_no_antes_e_depois(client, tokens, db, saude):
    saude(ativo=True, forcar=True, ordem=1)
    assert client.put(URL, json={"ordem": 7}, headers=tokens["admin"]).status_code == 200
    d = _ultima(db, "agenda_alterada").detalhe
    assert d["antes"]["ordem"] == 1 and d["depois"]["ordem"] == 7
    assert d["antes"]["forcar"] is True and d["depois"]["forcar"] is True


# ---------- #61 ciclo 2 ----------
def _n_auditoria(db):
    from sqlalchemy import func
    db.expire_all()
    return db.scalar(select(func.count()).select_from(Auditoria).where(Auditoria.alvo == "agendamento:saude"))


@pytest.mark.parametrize("valor", ["yes", "true", "false", "no", "1", "0", 1, 0, "", [], {}])
def test_r1_ativo_strictbool_422_sem_mudar_sem_auditar(client, tokens, db, saude, valor):
    saude(ativo=True)
    antes, n = _linha(db), _n_auditoria(db)
    r = client.put(URL, json={"ativo": valor}, headers=tokens["admin"])
    assert r.status_code == 422, r.text
    assert _linha(db) == antes and _n_auditoria(db) == n


@pytest.mark.parametrize("ativo_atual", [True, False])
def test_r1_ativo_bool_de_verdade_continua_valendo(client, tokens, db, saude, ativo_atual):
    saude(ativo=ativo_atual)
    assert client.put(URL, json={"ativo": not ativo_atual}, headers=tokens["admin"]).status_code == 200
    assert _linha(db)[1] is (not ativo_atual)
    assert _ultima(db, "agenda_alterada") is not None


@pytest.mark.parametrize("corpo", [{}, {"forcar": None}, {"horario": None, "ativo": None, "ordem": None},
                                   {"horario": "01:00"}, {"ordem": 1}, {"ativo": True},
                                   {"horario": "01:00", "ordem": 1, "ativo": True}])
def test_r2_put_sem_mudanca_200_sem_auditoria(client, tokens, db, saude, corpo):
    saude(ativo=True)
    antes, n = _linha(db), _n_auditoria(db)
    r = client.put(URL, json=corpo, headers=tokens["admin"])
    assert r.status_code == 200, r.text
    assert _linha(db) == antes and _n_auditoria(db) == n


def test_r2_mudanca_real_continua_auditada(client, tokens, db, saude):
    saude(ativo=True)
    n = _n_auditoria(db)
    assert client.put(URL, json={"horario": "02:30", "ativo": True}, headers=tokens["admin"]).status_code == 200
    assert _linha(db)[0] == "02:30" and _n_auditoria(db) == n + 1
    a = _ultima(db, "agenda_alterada")
    assert a.detalhe["antes"]["horario"] == "01:00" and a.detalhe["depois"]["horario"] == "02:30"
