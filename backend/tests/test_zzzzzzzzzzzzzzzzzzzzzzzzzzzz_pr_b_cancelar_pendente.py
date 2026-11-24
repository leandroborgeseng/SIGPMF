"""PR-B QA A1: fluxo real (sem mock) do "Cancelar execução pendente": PUT forcar=false e releitura de GET /api/agenda,
como a tela faz. TestClient contra o banco *_test; dados sintéticos (atualizado_por ZZTESTE)."""
from sqlalchemy import select, update

from app.models.controle import Agendamento
from tests.test_zzzzzzz_agenda import _zera_agenda


def _item_api(client, tokens, conector):
    r = client.get("/api/agenda", headers=tokens["admin"])
    assert r.status_code == 200
    return next(i for i in r.json()["itens"] if i["conector"] == conector)


def test_a1_put_forcar_false_e_agenda_relida_confirma(client, tokens, db):
    conector = db.scalar(select(Agendamento.conector).order_by(Agendamento.id).limit(1))
    _zera_agenda(db, ativo=False)
    db.execute(update(Agendamento).where(Agendamento.conector == conector)
               .values(ativo=True, forcar=True, atualizado_por="ZZTESTE"))
    db.commit()
    assert _item_api(client, tokens, conector)["forcar"] is True  # selo "Execução pendente" na tela
    r = client.put(f"/api/agenda/{conector}", json={"forcar": False}, headers=tokens["admin"])
    assert r.status_code == 200, r.text
    assert _item_api(client, tokens, conector)["forcar"] is False  # com o #58 o campo é aplicado de verdade
    db.expire_all()
    assert db.scalar(select(Agendamento.forcar).where(Agendamento.conector == conector)) is False
    _zera_agenda(db, ativo=False)
