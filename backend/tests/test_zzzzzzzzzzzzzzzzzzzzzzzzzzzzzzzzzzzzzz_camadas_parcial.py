# Só dados sintéticos ZZTESTE, banco *_test. Nunca lê a origem real nem chama a SIL.
"""TL 30/10 (PP1–PP5): camadas da leitura parcial. Prata e ouro só da bronze, sem ler a origem, checkpoint
intacto (concluida=f), status 'parcial', idempotente, roda com a agenda desligada, 409 com outra execução viva,
só admin e auditado com usuario_id."""
import pytest
from sqlalchemy import func, select, update

from app.models.camadas import Vinculo
from app.models.controle import Agendamento, Auditoria, CheckpointCarga, Execucao
from app.services import carga_completa as ccomp
from app.services import carga_conector as cc
from tests.conftest import TEST_URL
from tests.test_zzz_carga_completa import _ultima, origem  # noqa: F401 - fixture reaproveitada
from tests.test_zzzz_carga_etapas import _bronze_assinatura, _carga_ok, _em_andamento

pytestmark = pytest.mark.agenda_real
URL = "/api/conectores/saude/completa/camadas-parcial"


@pytest.fixture
def parcial(client, tokens, origem, db, monkeypatch):  # noqa: F811
    assert "_test" in TEST_URL.rsplit("/", 1)[-1].split("?")[0]

    def sincrono(ex_id, conector, trava, somente_camadas=False, parcial=False):
        ccomp.rodar(ex_id, conector, trava, leitor=origem[cc.CONECTORES[conector][0]],
                    somente_camadas=somente_camadas, parcial=parcial)
    monkeypatch.setattr(ccomp, "disparar", sincrono)
    ativo = db.scalar(select(Agendamento.ativo).where(Agendamento.conector == "saude"))
    db.execute(update(Agendamento).where(Agendamento.conector == "saude").values(ativo=True))  # CT26: completa exige ativo
    db.commit()
    ex = _carga_ok(client, tokens, db)
    ex = db.get(Execucao, ex.id)
    ex.status = "falha"  # leitura interrompida por erro da origem (como a SIL na 102493)
    db.execute(update(CheckpointCarga).where(CheckpointCarga.sistema == "SAUDE").values(concluida=False))
    db.commit()
    yield origem
    db.execute(update(Agendamento).where(Agendamento.conector == "saude").values(ativo=ativo, forcar=False))
    db.commit()


def _cps(db):
    db.expire_all()
    return sorted((c.tabela, c.posicao, c.concluida, repr(c.ultima_chave))
                  for c in db.scalars(select(CheckpointCarga).where(CheckpointCarga.sistema == "SAUDE")))


def _prata(db):
    db.expire_all()
    return db.scalar(select(func.count()).select_from(Vinculo).where(Vinculo.sistema == "SAUDE"))


def test_pp1_a_pp4_sem_origem_checkpoint_intacto_parcial_idempotente(client, tokens, parcial, db, monkeypatch):
    s = client.get("/api/conectores/saude/completa", headers=tokens["admin"]).json()
    assert s["execucao"]["pode_camadas_parcial"] is True and s["execucao"]["pode_retomar_camadas"] is False
    bronze, cps = _bronze_assinatura(db, "SAUDE"), _cps(db)
    assert all(not c[2] for c in cps)

    def proibido(*a, **k):
        raise AssertionError("a origem não pode ser lida nas camadas da leitura parcial")
    o = parcial["SAUDE"]
    o.sqls.clear()
    monkeypatch.setattr(ccomp, "leitor_padrao", proibido)
    monkeypatch.setattr(type(o), "abrir", proibido)
    contagens = []
    for _ in range(2):  # PP4: duas rodadas
        r = client.post(URL, headers=tokens["admin"])
        assert r.status_code == 202, r.text
        ex = _ultima(db, "SAUDE")
        assert ex.status == "parcial" and ex.status != "sucesso", ex.mensagem  # PP2
        assert "Camadas sobre leitura parcial (até " in ex.mensagem  # PP3
        assert ex.lidos == 0 and o.sqls == []  # PP1
        assert _bronze_assinatura(db, "SAUDE") == bronze and _cps(db) == cps  # bronze e checkpoint intactos
        contagens.append(_prata(db))
    assert contagens[0] > 0 and contagens[0] == contagens[1]  # PP4: não duplica


def test_pp5_roda_com_agenda_desligada_e_409_com_execucao_viva(client, tokens, parcial, db):
    db.execute(update(Agendamento).where(Agendamento.conector == "saude").values(ativo=False, forcar=False))
    db.commit()
    assert client.post(URL, headers=tokens["admin"]).status_code == 202
    assert _ultima(db, "SAUDE").status == "parcial"
    db.expire_all()
    assert db.scalar(select(Agendamento.forcar).where(Agendamento.conector == "saude")) is False
    trava = db.get_bind().connect()  # outro processo segurando a trava do conector
    trava.execute(ccomp.text("select pg_advisory_lock(:k)"), {"k": ccomp._chave_lock("SAUDE")})
    try:
        _em_andamento(db, "SAUDE")
        assert client.post(URL, headers=tokens["admin"]).status_code == 409
    finally:
        trava.execute(ccomp.text("select pg_advisory_unlock(:k)"), {"k": ccomp._chave_lock("SAUDE")})
        trava.close()
        db.execute(update(Execucao).where(Execucao.sistema == "SAUDE", Execucao.status == "em_andamento")
                   .values(status="interrompida"))
        db.commit()
    a = db.scalar(select(Auditoria).where(Auditoria.acao == "camadas_parcial_conflito").order_by(Auditoria.id.desc()))
    assert a is not None and "usuario_id" in (a.detalhe or {})


def test_so_admin_e_auditado(client, tokens, parcial, db):
    nao_admin = next(k for k in tokens if k != "admin")
    assert client.post(URL, headers=tokens[nao_admin]).status_code == 403
    assert client.post(URL, headers=tokens["admin"]).status_code == 202
    db.expire_all()
    a = db.scalar(select(Auditoria).where(Auditoria.acao == "camadas_parcial_iniciada").order_by(Auditoria.id.desc()))
    assert a is not None and a.detalhe.get("usuario_id") and a.usuario == "admin@aion.local"


def test_recusa_com_leitura_concluida(client, tokens, parcial, db):
    db.execute(update(CheckpointCarga).where(CheckpointCarga.sistema == "SAUDE").values(concluida=True))
    db.commit()
    assert client.post(URL, headers=tokens["admin"]).status_code == 409


def test_m1_ate_usa_a_chave_do_checkpoint_quando_ha(db, parcial):
    db.execute(update(CheckpointCarga).where(CheckpointCarga.sistema == "SAUDE").values(posicao=300, ultima_chave=[101733]))
    db.commit()
    assert ccomp.posicao_parcial(db, "SAUDE") == 101733
