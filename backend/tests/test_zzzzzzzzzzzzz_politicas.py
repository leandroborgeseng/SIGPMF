"""Onda 7 — políticas de dados e retenção (PL1–PL4): só versões antigas da bronze e logs técnicos; nada mais."""
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, text

from app.models.camadas import RegistroBronze
from app.models.controle import Auditoria, Execucao, Log
from app.services import retencao

S = "ZZRET"


def _bronze(db, io, h, dias, quarentena=None):
    eid = db.execute(text("INSERT INTO controle.execucoes (tipo, sistema, status) VALUES ('teste', :s, 'sucesso') RETURNING id"),
                     {"s": S}).scalar()
    db.execute(text("INSERT INTO bronze.registros (sistema_origem, tabela, id_origem, dados, hash, extraido_em, quarentena, "
                    "execucao_id) VALUES (:s, 'T', :io, '{}'::jsonb, :h, :e, :q, :x)"),
               {"s": S, "io": io, "h": h, "e": datetime.now(UTC) - timedelta(days=dias), "q": quarentena, "x": eid})


def _versoes(db, io):
    return [r.hash for r in db.scalars(select(RegistroBronze).where(RegistroBronze.sistema_origem == S,
                                                                    RegistroBronze.id_origem == io).order_by(RegistroBronze.id))]


def test_pl2_apaga_so_versoes_antigas_substituidas_e_logs_velhos(db):
    _bronze(db, "A", "a1", 30)   # antiga e substituída -> apaga
    _bronze(db, "A", "a2", 20)   # antiga e substituída -> apaga
    _bronze(db, "A", "a3", 15)   # versão ATUAL, mesmo velha -> fica
    _bronze(db, "B", "b1", 2)    # substituída mas recente -> fica
    _bronze(db, "B", "b2", 1)
    _bronze(db, "C", "c1", 40)   # única versão -> fica
    _bronze(db, "Q", "q1", 40, quarentena="chave_nula")
    _bronze(db, "Q", "q2", 1)    # quarentena antiga não é apagada por aqui
    db.add(Log(nivel="INFO", mensagem="log velho", criado_em=datetime.now(UTC) - timedelta(days=200)))
    db.add(Log(nivel="INFO", mensagem="log novo"))
    db.commit()
    aud_antes = db.scalar(select(func.count()).select_from(Auditoria))
    ex = retencao.executar(db, "admin@aion.local")
    assert ex.status == "sucesso", ex.mensagem
    assert _versoes(db, "A") == ["a3"] and _versoes(db, "B") == ["b1", "b2"] and _versoes(db, "C") == ["c1"]
    assert _versoes(db, "Q") == ["q1", "q2"]
    assert db.scalar(select(func.count()).select_from(Log).where(Log.mensagem == "log velho")) == 0
    assert db.scalar(select(func.count()).select_from(Log).where(Log.mensagem == "log novo")) == 1
    assert db.scalar(select(func.count()).select_from(Auditoria)) == aud_antes + 1  # auditoria nunca diminui
    assert retencao.ja_rodou_hoje(db)
    again = retencao.executar(db, "admin@aion.local")  # idempotente
    assert again.status == "sucesso" and _versoes(db, "A") == ["a3"]


def test_pl1_api_politicas_e_executar_so_admin(client, tokens, db):
    retencao.executar(db, "admin@aion.local")
    r = client.get("/api/politicas", headers=tokens["leitura"])
    assert r.status_code == 200
    d = r.json()
    assert len(d["regras"]) == 4 and any(c["classe"] == "sensivel" for c in d["conjuntos"])
    assert d["retencao"]["bronze_versoes_dias"] == 7 and d["retencao"]["ultima"] is not None
    assert all(len(p["celulas"]) == len(d["areas"]) for p in d["perfis"])
    assert client.post("/api/politicas/retencao/executar", headers=tokens["operador"]).status_code == 403
    r = client.post("/api/politicas/retencao/executar", headers=tokens["admin"])
    assert r.status_code == 200 and r.json()["status"] == "sucesso"
    ult = db.scalar(select(Execucao).where(Execucao.tipo == "retencao").order_by(Execucao.id.desc()))
    assert ult.usuario == "admin@aion.local"


def test_pl3_agenda_roda_retencao_uma_vez_por_dia(db, monkeypatch):
    from app.core.config import get_settings
    from app.services import agenda
    monkeypatch.setenv("AGENDA_HABILITADA", "true")
    monkeypatch.setenv("AGENDA_JANELA", "00:00-24:00")
    get_settings.cache_clear()
    from sqlalchemy import update

    from app.models.controle import Agendamento
    db.execute(update(Agendamento).values(ativo=False, forcar=False))
    db.execute(text("DELETE FROM controle.execucoes WHERE tipo = 'retencao' AND iniciado_em >= now() - interval '2 days'"))
    db.commit()
    agora = datetime.now(UTC)
    assert agenda.ciclo(db, agora=agora) == [("retencao", "sucesso")]
    assert agenda.ciclo(db, agora=agora) == []  # já rodou hoje
    get_settings.cache_clear()
