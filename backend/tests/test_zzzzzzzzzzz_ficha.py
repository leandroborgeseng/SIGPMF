"""Onda 5 — ficha do cidadão: abertura auditada e lista de quem consultou (FC4)."""
from sqlalchemy import select

from app.models.camadas import CidadaoSistemas
from app.models.controle import Auditoria


def _um_cidadao(db):
    return db.scalar(select(CidadaoSistemas.cidadao_id).limit(1))


def test_fc4_ficha_auditada_e_acessos_so_para_admin(client, tokens, db):
    cid = _um_cidadao(db)
    if cid is None:  # ordem dos testes: garante um cidadão no ouro
        import pytest
        pytest.skip("sem cidadão no ouro")
    assert client.get(f"/api/gold/cidadaos/{cid}", headers=tokens["leitura"]).status_code == 200
    ult = db.scalar(select(Auditoria).where(Auditoria.acao == "consulta_ficha").order_by(Auditoria.id.desc()))
    assert ult.alvo == f"cidadao:{cid}" and ult.usuario == "leitura@aion.local"
    assert client.get(f"/api/gold/cidadaos/{cid}/acessos", headers=tokens["leitura"]).status_code == 403
    r = client.get(f"/api/gold/cidadaos/{cid}/acessos", headers=tokens["admin_lgpd"])
    assert r.status_code == 200
    a = r.json()["acessos"]
    assert a and a[0]["o_que"] == "Abriu a ficha" and a[0]["usuario"] == "leitura@aion.local"
    assert client.get("/api/gold/cidadaos/nao-existe/acessos", headers=tokens["admin"]).status_code == 404
