"""PUT /api/agenda/{conector} com forcar=false: limpa um "Executar agora" pendente. Só dados sintéticos (ZZTESTE)."""
from datetime import UTC, datetime

from sqlalchemy import create_engine, select, text, update

from app.models.controle import Agendamento, Auditoria
from app.services import agenda as ag
from tests.conftest import TEST_URL
from tests.test_zzzzzzz_agenda import _zera_agenda

URL = "/api/agenda/sil"


def _item(db, conector="sil"):
    db.expire_all()
    return db.scalar(select(Agendamento).where(Agendamento.conector == conector))


def _pendente(db, ativo=True, forcar=True, conector="sil"):
    _zera_agenda(db, ativo=False)
    db.execute(update(Agendamento).where(Agendamento.conector == conector)
               .values(ativo=ativo, forcar=forcar, atualizado_por="ZZTESTE"))
    db.commit()


def _auds(db):
    db.expire_all()
    return list(db.scalars(select(Auditoria).where(Auditoria.acao == "agenda_forcar_limpo",
                                                   Auditoria.alvo == "agendamento:sil").order_by(Auditoria.id)))


def test_limpar_forcar_admin_auditado_e_proxima_some(client, tokens, db):
    _pendente(db)
    agora = datetime(2026, 10, 29, 15, 0, tzinfo=UTC)  # 12:00 BRT, fora da janela 00:00-06:00
    assert ag.proxima(_item(db), agora, 0, 360) == agora  # pendente => próxima é agora
    assert ag.devido(_item(db), agora, 0, 360)
    n = len(_auds(db))
    r = client.put(URL, json={"forcar": False}, headers=tokens["admin"])
    assert r.status_code == 200
    it = _item(db)
    assert it.forcar is False and it.ativo is True
    p = ag.proxima(it, agora, 0, 360)
    assert p is not None and p > agora  # a próxima causada pelo forcar sumiu (volta ao horário da janela)
    assert not ag.devido(it, agora, 0, 360)  # fora da janela, sem forcar, não roda
    aud = _auds(db)
    assert len(aud) == n + 1
    a = aud[-1]
    assert a.usuario and "@" in a.usuario and a.criado_em is not None
    assert a.detalhe["conector"] == "sil" and a.detalhe["antes"] == {"forcar": True}
    assert a.detalhe["depois"] == {"forcar": False}
    _zera_agenda(db, ativo=False)


def test_limpar_forcar_idempotente_tambem_audita(client, tokens, db):
    _pendente(db, forcar=False)
    n = len(_auds(db))
    for _ in range(2):
        assert client.put(URL, json={"forcar": False}, headers=tokens["admin"]).status_code == 200
    aud = _auds(db)
    assert len(aud) == n + 2 and all(a.detalhe["antes"] == {"forcar": False} for a in aud[-2:])
    assert _item(db).forcar is False
    _zera_agenda(db, ativo=False)


def test_forcar_true_422_sem_mudar(client, tokens, db):
    _pendente(db, forcar=False)
    r = client.put(URL, json={"forcar": True}, headers=tokens["admin"])
    assert r.status_code == 422
    assert _item(db).forcar is False
    _zera_agenda(db, ativo=False)


def test_nao_admin_403(client, tokens, db):
    _pendente(db)
    n = len(_auds(db))
    for perfil in ("operador", "leitura"):
        assert client.put(URL, json={"forcar": False}, headers=tokens[perfil]).status_code == 403
    assert _item(db).forcar is True and len(_auds(db)) == n
    _zera_agenda(db, ativo=False)


def test_limpar_forcar_com_trava_sil_ocupada(client, tokens, db):
    """Com a trava amostra:SIL ocupada, ligar devolve 409 AgendaTravada, mas {forcar:false} sozinho passa."""
    _pendente(db, ativo=False)
    eng = create_engine(TEST_URL)
    carga = eng.connect()
    try:
        assert carga.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": ag.CHAVE_TRAVA_SIL}).scalar()
        r = client.put(URL, json={"ativo": True}, headers=tokens["admin"])
        assert r.status_code == 409 and r.json()["detail"] == ag.MSG_SIL_TRAVADA
        r = client.put(URL, json={"forcar": False}, headers=tokens["admin"])
        assert r.status_code == 200
        assert _item(db).forcar is False
    finally:
        carga.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": ag.CHAVE_TRAVA_SIL})
        carga.close()
        eng.dispose()
    _zera_agenda(db, ativo=False)


def test_ativo_false_com_forcar_true_sem_proxima(db):
    _pendente(db, ativo=False, forcar=True)
    it = _item(db)
    agora = datetime.now(UTC)
    assert ag.proxima(it, agora, 0, 1440) is None
    assert ag.devido(it, agora, 0, 1440) is False
    est = ag.estado(db)
    sil = next(i for i in est["itens"] if i["conector"] == "sil")
    assert sil["proxima_em"] is None
    _zera_agenda(db, ativo=False)


def test_limpar_junto_com_outros_campos(client, tokens, db):
    _pendente(db, conector="saude")
    r = client.put("/api/agenda/saude", json={"ordem": 7, "forcar": False}, headers=tokens["admin"])
    assert r.status_code == 200
    it = _item(db, "saude")
    assert it.forcar is False and it.ordem == 7
    _zera_agenda(db, ativo=False)
