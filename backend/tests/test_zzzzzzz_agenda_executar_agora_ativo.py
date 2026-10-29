"""#32 (ALTO-3): "Executar agora" respeita ativo=false e o desliga geral (AGENDA_HABILITADA). Só dados sintéticos."""
from sqlalchemy import delete, func, select, update

from app.core.config import get_settings
from app.models.controle import Agendamento, Auditoria, CheckpointCarga, Execucao
from tests.test_zzzzzzz_agenda import _zera_agenda

URL = "/api/agenda/sil/executar-agora"


def _foto(db):
    db.expire_all()
    execs = db.scalar(select(func.count()).select_from(Execucao))
    cps = [(c.sistema, c.tabela, c.ambiente, c.posicao, c.ultima_chave, c.concluida, c.execucao_id, c.atualizado_em)
           for c in db.scalars(select(CheckpointCarga).order_by(CheckpointCarga.id))]
    item = db.scalar(select(Agendamento).where(Agendamento.conector == "sil"))
    return execs, cps, item.forcar


def _ultima_recusa(db):
    return db.scalar(select(Auditoria).where(Auditoria.acao == "agenda_executar_agora_recusada")
                     .order_by(Auditoria.id.desc()))


def _sem_segredo(aud):
    texto = repr(aud.detalhe).lower()
    for proibido in ("senha", "password", "token", "secret", "dsn", "postgresql", "mssql"):
        assert proibido not in texto


def _ativo(db, valor):
    _zera_agenda(db, ativo=False)
    if not db.scalar(select(CheckpointCarga).where(CheckpointCarga.tabela == "tabela_sintetica_32")):
        db.add(CheckpointCarga(sistema="SIL", tabela="tabela_sintetica_32", ambiente="sandbox", posicao=49,
                               ultima_chave=[49], concluida=False))
    db.execute(update(Agendamento).where(Agendamento.conector == "sil").values(ativo=valor, forcar=False))
    db.commit()


def _limpa(db):
    db.execute(delete(CheckpointCarga).where(CheckpointCarga.tabela == "tabela_sintetica_32"))
    db.commit()
    _zera_agenda(db, ativo=False)


def test_32_ativo_false_409_sem_execucao_checkpoint_intacto_e_auditado(client, tokens, db, monkeypatch):
    monkeypatch.setenv("AGENDA_HABILITADA", "true")
    get_settings.cache_clear()
    _ativo(db, False)
    antes = _foto(db)
    assert any(c[1] == "tabela_sintetica_32" and c[3] == 49 for c in antes[1])
    r = client.post(URL, headers=tokens["admin"])
    assert r.status_code == 409 and "desligado" in r.json()["detail"]
    assert _foto(db) == antes  # nenhuma execução, checkpoint e forcar intactos
    aud = _ultima_recusa(db)
    assert aud.alvo == "agendamento:sil" and aud.detalhe == {"motivo": "desligado", "conector": "sil"}
    assert aud.usuario and "@" in aud.usuario
    _sem_segredo(aud)
    _limpa(db)
    get_settings.cache_clear()


def test_32_desliga_geral_409_sem_execucao_e_auditado(client, tokens, db, monkeypatch):
    monkeypatch.setenv("AGENDA_HABILITADA", "false")
    get_settings.cache_clear()
    _ativo(db, True)  # mesmo com o item ativo, o desliga geral recusa
    antes = _foto(db)
    assert any(c[1] == "tabela_sintetica_32" and c[3] == 49 for c in antes[1])
    r = client.post(URL, headers=tokens["admin"])
    assert r.status_code == 409 and "AGENDA_HABILITADA" in r.json()["detail"]
    assert _foto(db) == antes
    aud = _ultima_recusa(db)
    assert aud.alvo == "agendamento:sil" and aud.detalhe == {"motivo": "agenda_desligada", "conector": "sil"}
    assert aud.usuario and "@" in aud.usuario
    _sem_segredo(aud)
    _limpa(db)
    get_settings.cache_clear()


def test_32_ativo_true_comportamento_mantido(client, tokens, db, monkeypatch):
    monkeypatch.setenv("AGENDA_HABILITADA", "true")
    get_settings.cache_clear()
    _ativo(db, True)
    execs, cps, _ = _foto(db)
    n_rec = db.scalar(select(func.count()).select_from(Auditoria).where(Auditoria.acao == "agenda_executar_agora_recusada"))
    r = client.post(URL, headers=tokens["admin"])
    assert r.status_code == 202
    item = next(i for i in r.json()["itens"] if i["conector"] == "sil")
    assert item["forcar"] is True
    assert _foto(db)[:2] == (execs, cps)  # só marca para o próximo ciclo; não abre execução na hora
    aud = db.scalar(select(Auditoria).where(Auditoria.acao == "agenda_executar_agora").order_by(Auditoria.id.desc()))
    assert aud.alvo == "agendamento:sil"
    assert db.scalar(select(func.count()).select_from(Auditoria)
                     .where(Auditoria.acao == "agenda_executar_agora_recusada")) == n_rec
    _limpa(db)
    get_settings.cache_clear()
