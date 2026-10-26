# Roda depois de test_zzz_carga_completa (bronze SAUDE/CEMITERIO sintética já gravada). Só dados sintéticos.
"""CC7: etapas da carga completa, sinal de vida em conexão separada, carga interrompida (startup e thread morta),
retomada só de prata/ouro sem reler a origem e regressão do índice da prata (mesmo resultado do algoritmo linear)."""
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, text

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.models.camadas import RegistroBronze, Vinculo
from app.models.controle import CheckpointCarga, Execucao, Log
from app.services import carga_completa as ccomp
from app.silver import processamento
from tests import sintetico_prata
from tests.test_zzz_carga_completa import _amostra, _ultima, origem  # noqa: F401 - fixture reaproveitada


def _bronze_assinatura(db, sistema):
    db.expire_all()
    n = db.scalar(select(func.count()).select_from(RegistroBronze).where(RegistroBronze.sistema_origem == sistema))
    h = db.scalar(text("select md5(string_agg(id::text || ':' || hash, ',' order by id)) from bronze.registros "
                       "where sistema_origem = :s"), {"s": sistema})
    return n, h


def _carga_ok(client, tokens, db, conector="saude"):
    _amostra(db, conector)
    assert client.post(f"/api/conectores/{conector}/completa", headers=tokens["admin"]).status_code == 202
    ex = _ultima(db, ccomp._sistema(conector))
    assert ex.status == "sucesso", ex.mensagem
    return ex


def _em_andamento(db, sistema, **kw):
    ex = Execucao(tipo=ccomp.TIPO, sistema=sistema, status="em_andamento", usuario="admin@aion.local", **kw)
    db.add(ex)
    db.commit()
    return ex


# ---------- CC7.1 / CC7.5 / API: etapas, contadores, atualizado_em e logs ----------
def test_cc7_api_etapas_e_logs_de_inicio_e_fim(client, tokens, origem, db):  # noqa: F811
    vistos = []
    original = ccomp.sinal_de_vida

    def espiao(ex_id, etapa, n, total):
        original(ex_id, etapa, n, total)
        outra = SessionLocal()  # outra conexão: enxerga o sinal enquanto a transação da prata ainda está aberta
        try:
            e = outra.get(Execucao, ex_id)
            vistos.append((e.etapa, e.processadas_etapa, e.total_etapa, e.atualizado_em is not None, e.status))
        finally:
            outra.close()
    pytest.MonkeyPatch.setattr(pytest.MonkeyPatch(), ccomp, "sinal_de_vida", espiao)
    try:
        ex = _carga_ok(client, tokens, db)
    finally:
        ccomp.sinal_de_vida = original
    assert vistos and all(v[0] == "prata" and v[3] and v[4] == "em_andamento" for v in vistos)
    assert vistos[-1][1] == vistos[-1][2] > 0  # contador da etapa chega ao total
    s = client.get("/api/conectores/saude/completa", headers=tokens["admin"]).json()["execucao"]
    for k in ("etapa", "processadas_etapa", "total_etapa", "atualizado_em"):
        assert k in s
    assert s["etapa"] == "concluida" and s["etapa_nome"] == "Concluída" and s["atualizado_em"]
    assert s["status_texto"] == "Concluída · Carga completa" and s["sem_progresso"] is False
    msgs = [m for (m,) in db.execute(select(Log.mensagem).where(Log.execucao_id == ex.id))]
    for nome in ("Lendo da origem", "Tratando (prata)", "Unificando cidadãos (ouro)"):
        assert any(m.startswith(f"Etapa {nome}: início") for m in msgs), nome
        assert any(m.startswith(f"Etapa {nome}: fim em") for m in msgs), nome


def test_cc7_status_legivel_em_andamento(client, tokens, db):
    ex = _em_andamento(db, "CEMITERIO", etapa="leitura", processadas_etapa=4, total_etapa=10,
                       atualizado_em=datetime.now(UTC))
    trava = db.get_bind().connect()
    trava.execute(text("select pg_advisory_lock(:k)"), {"k": ccomp._chave_lock("CEMITERIO")})
    try:
        s = client.get("/api/conectores/cemiterio/completa", headers=tokens["admin"]).json()["execucao"]
        assert s["status_texto"] == "Em andamento · Carga completa" and s["etapa_nome"] == "Lendo da origem"
        assert (s["processadas_etapa"], s["total_etapa"]) == (4, 10) and s["alerta_min"] == 5
    finally:
        trava.execute(text("select pg_advisory_unlock(:k)"), {"k": ccomp._chave_lock("CEMITERIO")})
        trava.close()
        db.refresh(ex)
        ex.status = "cancelada"
        db.commit()


# ---------- CC7.3: sem progresso (env, padrão 5 min) ----------
def test_cc7_3_sem_progresso_por_env_e_thread_viva(client, tokens, db, monkeypatch):
    ex = _em_andamento(db, "CEMITERIO", etapa="prata", atualizado_em=datetime.now(UTC) - timedelta(minutes=10))
    trava = db.get_bind().connect()  # processo vivo segura a trava
    trava.execute(text("select pg_advisory_lock(:k)"), {"k": ccomp._chave_lock("CEMITERIO")})
    try:
        s = client.get("/api/conectores/cemiterio/completa", headers=tokens["admin"]).json()["execucao"]
        assert s["status"] == "em_andamento" and s["sem_progresso"] is True
        monkeypatch.setenv("SIGPMF_CARGA_ALERTA_MIN", "30")
        get_settings.cache_clear()
        s = client.get("/api/conectores/cemiterio/completa", headers=tokens["admin"]).json()["execucao"]
        assert s["sem_progresso"] is False and s["alerta_min"] == 30
    finally:
        get_settings.cache_clear()
        trava.execute(text("select pg_advisory_unlock(:k)"), {"k": ccomp._chave_lock("CEMITERIO")})
        trava.close()
    monkeypatch.delenv("SIGPMF_CARGA_ALERTA_MIN")
    get_settings.cache_clear()
    # trava livre + sem progresso = thread morta: vira "Carga interrompida"
    s = client.get("/api/conectores/cemiterio/completa", headers=tokens["admin"]).json()["execucao"]
    assert s["status"] == "interrompida" and s["status_texto"] == "Carga interrompida" and s["sem_progresso"] is False
    db.expire_all()
    assert db.get(Execucao, ex.id).status == "interrompida"


# ---------- TL: startup marca em_andamento sem processo vivo como interrompida ----------
def test_startup_em_andamento_sem_processo_vivo_vira_interrompida(client, tokens, db):
    from app.main import interromper_cargas_orfas
    orfa = _em_andamento(db, "SAUDE", etapa="prata", atualizado_em=datetime.now(UTC))
    viva = _em_andamento(db, "CEMITERIO", etapa="leitura", atualizado_em=datetime.now(UTC))
    trava = db.get_bind().connect()  # Cemitério tem processo vivo (trava segura): não mexe
    trava.execute(text("select pg_advisory_lock(:k)"), {"k": ccomp._chave_lock("CEMITERIO")})
    try:
        assert interromper_cargas_orfas() == 1
        db.expire_all()
        assert db.get(Execucao, orfa.id).status == "interrompida"
        assert db.get(Execucao, viva.id).status == "em_andamento"
        s = client.get("/api/conectores/saude/completa", headers=tokens["admin"]).json()["execucao"]
        assert s["status"] == "interrompida" and s["status_texto"] == "Carga interrompida"
        assert "Carga interrompida" in s["mensagem"]
        assert db.scalar(select(func.count()).select_from(Log).where(Log.execucao_id == orfa.id, Log.nivel == "WARN")) == 1
    finally:
        trava.execute(text("select pg_advisory_unlock(:k)"), {"k": ccomp._chave_lock("CEMITERIO")})
        trava.close()
        v = db.get(Execucao, viva.id)
        v.status = "cancelada"
        db.commit()


def test_startup_lifespan_chama_interromper(monkeypatch):
    import app.main as m
    chamado = []
    monkeypatch.setattr(m, "interromper_cargas_orfas", lambda: chamado.append(1) or 0)
    from fastapi.testclient import TestClient
    with TestClient(m.create_app()):
        pass
    assert chamado == [1]


# ---------- TL: retomar prata/ouro não relê a origem e não altera a bronze ----------
def test_retomar_camadas_sem_reler_origem_e_bronze_inalterada(client, tokens, origem, db, monkeypatch):  # noqa: F811
    ex = _carga_ok(client, tokens, db)
    assert client.post("/api/conectores/saude/completa/retomar-camadas",
                       headers=tokens["admin"]).status_code == 409  # não interrompida: recusa
    ex = db.get(Execucao, ex.id)
    ex.status = "interrompida"  # simula a thread morta na prata (bronze toda gravada)
    db.commit()
    db.execute(text("delete from silver.vinculos where sistema = 'SAUDE'"))  # prata parcial/antiga
    db.commit()
    s = client.get("/api/conectores/saude/completa", headers=tokens["admin"]).json()
    assert s["execucao"]["pode_retomar_camadas"] is True
    antes = _bronze_assinatura(db, "SAUDE")
    cps_antes = sorted((c.tabela, c.posicao, c.concluida, repr(c.ultima_chave)) for c in db.scalars(select(CheckpointCarga)))
    o = origem["SAUDE"]
    o.sqls.clear()

    def proibido(*a, **k):
        raise AssertionError("conector não pode ser chamado na retomada de prata/ouro")
    monkeypatch.setattr(ccomp, "leitor_padrao", proibido)
    monkeypatch.setattr(type(o), "abrir", proibido)
    r = client.post("/api/conectores/saude/completa/retomar-camadas", headers=tokens["admin"])
    assert r.status_code == 202, r.text
    novo = _ultima(db, "SAUDE")
    assert novo.id != ex.id and novo.status == "sucesso" and novo.etapa == "concluida", novo.mensagem
    assert o.sqls == [] and novo.lidos == 0  # nada lido da origem
    assert _bronze_assinatura(db, "SAUDE") == antes  # contagem e hash da bronze iguais
    db.expire_all()
    assert sorted((c.tabela, c.posicao, c.concluida, repr(c.ultima_chave))
                  for c in db.scalars(select(CheckpointCarga))) == cps_antes
    assert db.scalar(select(func.count()).select_from(Vinculo).where(Vinculo.sistema == "SAUDE")) > 0  # prata refeita
    assert any("sem reler a origem" in m for (m,) in db.execute(select(Log.mensagem).where(Log.execucao_id == novo.id)))


# ---------- Regressão: índice da prata == algoritmo linear antigo ----------
def _compara(db):
    processamento.reconstruir(db, indexado=False)
    antigo = sintetico_prata.snapshot(db)
    processamento.reconstruir(db, indexado=True)
    novo = sintetico_prata.snapshot(db)
    return antigo, novo


def test_regressao_indice_igual_ao_linear_no_fixture(db):
    try:
        antigo, novo = _compara(db)  # bronze do fixture saude+cemiterio (óbitos, CNS, terceiros, e-SUS)
        assert antigo["vinculos"] and antigo == novo
    finally:
        db.rollback()


def test_regressao_indice_igual_ao_linear_sintetico_com_colisoes(db):
    try:
        sintetico_prata.gera(db, 1500)
        antigo, novo = _compara(db)
        assert any(p[3] == "revisao" for p in antigo["pendentes"])  # há colisões de verdade
        assert any(v[4] == "novo_sem_cpf" and 0 < v[5] < 0.85 for v in antigo["vinculos"])  # melhor parcial gravado
        assert antigo == novo
    finally:
        db.rollback()
