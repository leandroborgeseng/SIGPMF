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


def test_33_desligar_cancela_executar_agora_pendente_e_audita(client, tokens, db, monkeypatch):
    """M1: Executar agora pendente + desligar -> forcar False e auditoria; religar não roda fora da janela."""
    from datetime import UTC, datetime
    from zoneinfo import ZoneInfo

    from app.services import agenda as ag

    monkeypatch.setenv("AGENDA_HABILITADA", "true")
    monkeypatch.setenv("AGENDA_JANELA", "00:00-06:00")
    get_settings.cache_clear()
    _ativo(db, True)
    assert client.post(URL, headers=tokens["admin"]).status_code == 202
    assert _foto(db)[2] is True
    execs = _foto(db)[0]
    r = client.put("/api/agenda/sil", headers=tokens["admin"], json={"ativo": False})
    assert r.status_code == 200
    assert next(i for i in r.json()["itens"] if i["conector"] == "sil")["forcar"] is False
    assert _foto(db)[2] is False
    aud = db.scalar(select(Auditoria).where(Auditoria.acao == "agenda_executar_agora_cancelado")
                    .order_by(Auditoria.id.desc()))
    assert aud.alvo == "agendamento:sil"
    assert aud.detalhe == {"motivo": "forcar_cancelado_desligado", "conector": "sil"}
    assert aud.usuario and "@" in aud.usuario
    _sem_segredo(aud)
    # religar às 12h (fora da janela 00:00-06:00): nada é devido e o ciclo não abre execução
    assert client.put("/api/agenda/sil", headers=tokens["admin"], json={"ativo": True}).status_code == 200
    meio_dia = datetime(2026, 10, 29, 12, 0, tzinfo=ZoneInfo("America/Sao_Paulo")).astimezone(UTC)
    db.expire_all()
    item = db.scalar(select(Agendamento).where(Agendamento.conector == "sil"))
    assert item.ativo is True and item.forcar is False
    assert ag.devido(item, meio_dia, *ag.janela("00:00-06:00")) is False
    assert ag.ciclo(db, agora=meio_dia, leitores={}, esperar=lambda *a, **k: None) == []
    assert _foto(db)[0] == execs
    _limpa(db)
    get_settings.cache_clear()


def test_33_desligar_sem_pendente_nao_audita_cancelamento(client, tokens, db, monkeypatch):
    monkeypatch.setenv("AGENDA_HABILITADA", "true")
    get_settings.cache_clear()
    _ativo(db, True)
    n = db.scalar(select(func.count()).select_from(Auditoria).where(Auditoria.acao == "agenda_executar_agora_cancelado"))
    assert client.put("/api/agenda/sil", headers=tokens["admin"], json={"ativo": False}).status_code == 200
    assert db.scalar(select(func.count()).select_from(Auditoria)
                     .where(Auditoria.acao == "agenda_executar_agora_cancelado")) == n
    _limpa(db)
    get_settings.cache_clear()


def test_33_conector_inexistente_404_mesmo_com_agenda_desligada_sem_auditoria(client, tokens, db, monkeypatch):
    """B1: 404 antes de olhar a agenda; o nome vindo da URL não entra na auditoria."""
    nome = "conector_inexistente_zz33"
    for habilitada in ("false", "true"):
        monkeypatch.setenv("AGENDA_HABILITADA", habilitada)
        get_settings.cache_clear()
        n = db.scalar(select(func.count()).select_from(Auditoria).where(Auditoria.acao == "agenda_executar_agora_recusada"))
        r = client.post(f"/api/agenda/{nome}/executar-agora", headers=tokens["admin"])
        assert r.status_code == 404
        db.expire_all()
        assert db.scalar(select(func.count()).select_from(Auditoria)
                         .where(Auditoria.acao == "agenda_executar_agora_recusada")) == n
        assert db.scalar(select(func.count()).select_from(Auditoria).where(Auditoria.alvo.contains(nome))) == 0
    get_settings.cache_clear()


# ------------------------------------------------------------------ #33 B2: corrida "Executar agora" x desligar
def _estado_sil(db):
    db.expire_all()
    it = db.scalar(select(Agendamento).where(Agendamento.conector == "sil"))
    return it.ativo, it.forcar


def test_33_b2_forcar_em_item_desligado_levanta_409_sem_marcar(db):
    from app.services import agenda as ag
    _ativo(db, False)
    try:
        ag.forcar(db, "sil", "admin@aion.local")
        raise AssertionError("esperava AgendaDesligada")
    except ag.AgendaDesligada:
        pass
    assert _estado_sil(db) == (False, False)
    _limpa(db)


def test_33_b2_desligar_trava_linha_e_forcar_concorrente_recusa(db):
    """Desligar segura a linha; o forcar() que chega junto espera, reavalia WHERE ativo e recusa."""
    import threading

    from app.core.db import SessionLocal
    from app.services import agenda as ag
    _ativo(db, True)
    s1 = SessionLocal()
    it = s1.scalar(select(Agendamento).where(Agendamento.conector == "sil").with_for_update())
    it.ativo, it.forcar = False, False
    res = {}

    def executar_agora():
        s2 = SessionLocal()
        try:
            ag.forcar(s2, "sil", "admin@aion.local")
            res["r"] = "ok"
        except ag.AgendaDesligada:
            res["r"] = "409"
        finally:
            s2.close()
    t = threading.Thread(target=executar_agora)
    t.start()
    t.join(0.5)
    assert t.is_alive()  # bloqueado pela trava do desligar
    s1.commit()
    s1.close()
    t.join(10)
    assert res["r"] == "409" and _estado_sil(db) == (False, False)
    _limpa(db)


def test_33_b2_corrida_simultanea_nunca_desligado_com_forcar(db):
    """'Executar agora' e 'desligar' ao mesmo tempo (20 rodadas): nunca termina ativo=False com forcar=True."""
    import threading

    from app.core.db import SessionLocal
    from app.services import agenda as ag
    for _ in range(20):
        _ativo(db, True)
        barreira = threading.Barrier(2)
        erros = []

        def executar_agora():
            s = SessionLocal()
            try:
                barreira.wait()
                ag.forcar(s, "sil", "admin@aion.local")
            except ag.AgendaDesligada:
                pass
            except Exception as exc:  # noqa: BLE001
                erros.append(exc)
            finally:
                s.close()

        def desligar():
            s = SessionLocal()
            try:
                barreira.wait()
                ag.atualizar(s, "sil", "admin@aion.local", ativo=False)
            except Exception as exc:  # noqa: BLE001
                erros.append(exc)
            finally:
                s.close()
        ts = [threading.Thread(target=executar_agora), threading.Thread(target=desligar)]
        for t in ts:
            t.start()
        for t in ts:
            t.join(10)
        assert not erros
        assert _estado_sil(db) == (False, False)
    _limpa(db)


def test_33_b2_rota_409_quando_desligado_em_paralelo(client, tokens, db, monkeypatch):
    """A checagem da rota passa (ativo), mas o item é desligado antes da gravação: 409 e auditoria de recusa."""
    from app.services import agenda as ag
    monkeypatch.setenv("AGENDA_HABILITADA", "true")
    get_settings.cache_clear()
    _ativo(db, True)
    original = ag.forcar

    def desliga_antes(sessao, conector, usuario):
        db.execute(update(Agendamento).where(Agendamento.conector == "sil").values(ativo=False))
        db.commit()
        return original(sessao, conector, usuario)
    monkeypatch.setattr(ag, "forcar", desliga_antes)
    r = client.post(URL, headers=tokens["admin"])
    assert r.status_code == 409
    assert _estado_sil(db) == (False, False)
    assert _ultima_recusa(db).detalhe == {"motivo": "desligado", "conector": "sil"}
    _limpa(db)
    get_settings.cache_clear()
