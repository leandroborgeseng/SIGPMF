"""Educação (GestaoEscolar + CoreSSO) na agenda das fontes prioritárias. Só dados sintéticos ZZTESTE."""
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import delete, select, update

from app.core.config import get_settings
from app.models.controle import Agendamento, Auditoria, Execucao
from app.services import agenda as ag
from app.services import carga_gestaoescolar as cge

URL = "/api/agenda/educacao/executar-agora"
MARCA = "ZZTESTE educação"


def _janela_fora() -> str:
    """Janela de 1 minuto 12 h longe de agora (fuso da agenda): agora fica fora dela."""
    local = datetime.now(ZoneInfo(get_settings().agenda_fuso)) + timedelta(hours=12)
    a, b = local, local + timedelta(minutes=1)
    return f"{a:%H:%M}-{b:%H:%M}" if b.strftime("%H:%M") > a.strftime("%H:%M") else "03:00-03:01"


def _env(monkeypatch, janela="00:00-24:00"):
    monkeypatch.setenv("AGENDA_HABILITADA", "true")
    monkeypatch.setenv("AGENDA_JANELA", janela)
    get_settings.cache_clear()


def _limpa(db):
    db.execute(delete(Execucao).where(Execucao.mensagem.like(f"{MARCA}%")))
    db.execute(update(Agendamento).where(Agendamento.conector == "educacao").values(ativo=False, forcar=False))
    db.commit()


def _exec(db, tipo, status, lidos=0):
    ex = Execucao(tipo=tipo, sistema=cge.SISTEMA, status=status, usuario="admin@aion.local", modo="amostra",
                  lidos=lidos, mensagem=f"{MARCA} {status}", iniciado_em=datetime.now(UTC))
    db.add(ex)
    db.commit()
    return ex


def _liga(db, valor=True):
    db.execute(update(Agendamento).where(Agendamento.conector == "educacao").values(ativo=valor, forcar=False))
    db.commit()


def _recusa(db):
    return db.scalar(select(Auditoria).where(Auditoria.acao == "agenda_executar_agora_recusada",
                                             Auditoria.alvo == "agendamento:educacao").order_by(Auditoria.id.desc()))


def test_migracao_educacao_desligada_por_padrao(client, tokens, db):
    _limpa(db)
    a = db.scalar(select(Agendamento).where(Agendamento.conector == "educacao"))
    assert a is not None and a.ativo is False and a.forcar is False and a.horario == "04:00"
    r = client.get("/api/agenda", headers=tokens["leitura"]).json()
    item = next(i for i in r["itens"] if i["conector"] == "educacao")
    assert item["nome"] == "Educação (GestaoEscolar + CoreSSO)" and item["ativo"] is False and item["proxima_em"] is None


def test_status_vem_da_ultima_execucao_real_e_zero_aparece(client, tokens, db):
    _limpa(db)
    _exec(db, cge.TIPO_AMOSTRA, "sucesso", lidos=0)
    item = next(i for i in client.get("/api/agenda", headers=tokens["leitura"]).json()["itens"] if i["conector"] == "educacao")
    real = item["ultima_execucao_real"]
    assert real["status"] == "sucesso" and real["lidos"] == 0
    _limpa(db)


def test_desligado_recusa_e_audita(client, tokens, db, monkeypatch):
    _env(monkeypatch)
    _limpa(db)
    r = client.post(URL, headers=tokens["admin"])
    assert r.status_code == 409 and "desligado" in r.json()["detail"]
    assert _recusa(db).detalhe == {"motivo": "desligado", "conector": "educacao"}
    get_settings.cache_clear()


def test_so_admin(client, tokens, db, monkeypatch):
    _env(monkeypatch)
    _limpa(db)
    _liga(db)
    for perfil in ("operador", "leitura"):
        assert client.post(URL, headers=tokens[perfil]).status_code == 403
    _limpa(db)
    get_settings.cache_clear()


def test_409_com_carga_em_andamento(client, tokens, db, monkeypatch):
    _env(monkeypatch)
    _limpa(db)
    _liga(db)
    _exec(db, cge.TIPO_AMOSTRA, "sucesso", lidos=5)
    _exec(db, cge.TIPO_COMPLETA, "em_andamento")
    r = client.post(URL, headers=tokens["admin"])
    assert r.status_code == 409 and "em andamento" in r.json()["detail"]
    assert _recusa(db).detalhe == {"motivo": "ocupado", "conector": "educacao"}
    assert db.scalar(select(Agendamento.forcar).where(Agendamento.conector == "educacao")) is False
    item = next(i for i in client.get("/api/agenda", headers=tokens["leitura"]).json()["itens"] if i["conector"] == "educacao")
    assert item["em_andamento"]["status"] == "em_andamento" and item["pode_executar_agora"] is False
    _limpa(db)
    get_settings.cache_clear()


def test_sem_amostra_recusa(client, tokens, db, monkeypatch):
    _env(monkeypatch)
    _limpa(db)
    _exec(db, cge.TIPO_AMOSTRA, "falha")  # última amostra sem sucesso
    _liga(db)
    r = client.post(URL, headers=tokens["admin"])
    assert r.status_code == 409 and r.json()["detail"] == cge.SEM_AMOSTRA
    assert _recusa(db).detalhe["motivo"] == "sem_amostra"
    _limpa(db)
    get_settings.cache_clear()


def test_fora_da_janela_recusa_e_mostra_motivo(client, tokens, db, monkeypatch):
    _env(monkeypatch, _janela_fora())
    _limpa(db)
    _liga(db)
    _exec(db, cge.TIPO_AMOSTRA, "sucesso", lidos=5)
    r = client.post(URL, headers=tokens["admin"])
    assert r.status_code == 409 and "janela noturna" in r.json()["detail"]
    assert _recusa(db).detalhe["motivo"] == "fora_janela"
    item = next(i for i in client.get("/api/agenda", headers=tokens["leitura"]).json()["itens"] if i["conector"] == "educacao")
    assert item["pode_executar_agora"] is False and "janela noturna" in item["motivo_executar_agora"]
    _limpa(db)
    get_settings.cache_clear()


def test_janela_num_lugar_so(db, monkeypatch):
    """A rota e a agenda usam cge.dentro_da_janela: trocar a regra ali (PR da env) muda as duas."""
    _limpa(db)
    _exec(db, cge.TIPO_AMOSTRA, "sucesso", lidos=5)
    monkeypatch.setattr(cge, "dentro_da_janela", lambda agora: (True, "x"))
    assert ag.educacao_recusa(db) is None
    monkeypatch.setattr(cge, "dentro_da_janela", lambda agora: (False, "00:00-06:00"))
    assert ag.educacao_recusa(db)[0] == "fora_janela"
    _limpa(db)


def test_executar_agora_ok_e_desligar_cancela_forcar(client, tokens, db, monkeypatch):
    _env(monkeypatch)
    _limpa(db)
    _liga(db)
    _exec(db, cge.TIPO_AMOSTRA, "sucesso", lidos=5)
    assert client.post(URL, headers=tokens["admin"]).status_code == 202
    assert db.scalar(select(Agendamento.forcar).where(Agendamento.conector == "educacao")) is True
    assert client.put("/api/agenda/educacao", json={"ativo": False}, headers=tokens["admin"]).status_code == 200
    db.expire_all()
    a = db.scalar(select(Agendamento).where(Agendamento.conector == "educacao"))
    assert a.ativo is False and a.forcar is False  # sem forcar órfão
    # forcar atômico: com ativo=false, a UPDATE ... WHERE ativo não muda nada
    try:
        ag.forcar(db, "educacao", "admin@aion.local")
        raise AssertionError("deveria recusar")
    except ag.AgendaDesligada:
        pass
    _limpa(db)
    get_settings.cache_clear()


def test_ciclo_forcado_fora_da_janela_nao_carrega(db, monkeypatch):
    monkeypatch.setenv("AGENDA_JANELA", _janela_fora())
    get_settings.cache_clear()
    _limpa(db)
    _exec(db, cge.TIPO_AMOSTRA, "sucesso", lidos=5)
    db.execute(update(Agendamento).where(Agendamento.conector != "educacao").values(ativo=False, forcar=False))
    db.execute(update(Agendamento).where(Agendamento.conector == "educacao").values(ativo=True, forcar=True))
    db.commit()
    antes = db.scalar(select(Execucao.id).order_by(Execucao.id.desc()).limit(1))
    feitos = ag.ciclo(db, agora=datetime.now(UTC), leitores={"educacao": object()})
    assert ("educacao", "bloqueada") in feitos
    assert db.scalar(select(Execucao.id).order_by(Execucao.id.desc()).limit(1)) == antes  # nenhuma carga
    _limpa(db)
    get_settings.cache_clear()
