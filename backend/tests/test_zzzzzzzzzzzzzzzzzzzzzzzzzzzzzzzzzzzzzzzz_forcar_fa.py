"""FA1–FA4 (forcar da agenda) e SL1 (teto de lotes só na amostra da SIL). Banco *_test, dados sintéticos ZZTESTE."""
from datetime import UTC, datetime

import pytest
from sqlalchemy import select, update

from app.models.controle import Agendamento, Execucao, Log
from app.services import agenda as ag
from app.services import carga_completa as ccomp
from app.services import carga_conector as cc
from tests.test_zzzzzzz_agenda import _amostras, _esperar, _leitores, _local, _zera_agenda, origem  # noqa: F401
from tests.test_zzzzzzzzzzzzzzzz_sil_unificado import Api, _con

SAUDE = "/api/agenda/saude"


def _item(db, conector="saude"):
    db.expire_all()
    return db.scalar(select(Agendamento).where(Agendamento.conector == conector))


def _forcado(db, conector="saude", ativo=True):
    _zera_agenda(db, ativo=False)
    db.execute(update(Agendamento).where(Agendamento.conector == conector)
               .values(ativo=ativo, forcar=True, proxima_tentativa_em=None, atualizado_por="ZZTESTE"))
    db.commit()


def _logs_forcar(db, conector="saude"):
    db.expire_all()
    return [m for m in db.scalars(select(Log.mensagem).where(Log.mensagem.like(f"[agenda_forcar] conector={conector} %"))
                                  .order_by(Log.id))]


# ---------------------------------------------------------------- FA4 (prioritário)

def test_fa4_ocupada_nao_limpa_forcar_reproduz_sil_17h22(db, origem, monkeypatch):  # noqa: F811
    """Causa do FA4: com uma carga manual em andamento, o ciclo acha forcar=t, iniciar() dá Conflito e o _encerra
    ('ocupada') zerava o forcar sem nenhuma carga criada a partir dele. Antes do fix: forcar=False aqui."""
    _amostras(db)
    _forcado(db)

    def ocupado(db, usuario, conector, somente_camadas=False, modo="completa"):
        raise cc.Conflito("ocupado")
    monkeypatch.setattr(ccomp, "iniciar", ocupado)
    agora = _local(2026, 10, 30, 17, 22)
    assert ag.ciclo(db, agora=agora, leitores=_leitores(origem), esperar=_esperar) == [("saude", "ocupada")]
    it = _item(db)
    assert it.forcar is True and it.ativo is True  # sobreviveu ao encerramento sem carga
    assert it.proxima_tentativa_em is not None      # e espera antes de tentar de novo (sem laço a cada ciclo)
    assert ag.ciclo(db, agora=agora, leitores=_leitores(origem), esperar=_esperar) == []
    _zera_agenda(db, ativo=False)


def test_fa4_bloqueada_e_sem_mudanca_nao_limpam_forcar(db, origem, monkeypatch):  # noqa: F811
    _forcado(db)
    monkeypatch.setattr(ccomp, "motivo_bloqueio", lambda db, conector: ccomp.SEM_AMOSTRA)
    assert ag.ciclo(db, agora=_local(2026, 10, 30, 17, 25), leitores=_leitores(origem), esperar=_esperar) \
        == [("saude", "bloqueada")]
    assert _item(db).forcar is True
    _zera_agenda(db, ativo=False)


def test_fa4_encerra_nunca_mexe_no_forcar(db):
    _forcado(db)
    it = _item(db)
    for status, resolvido in (("sucesso", True), ("falha", False), ("sem_mudanca", True)):
        ag._encerra(db, it, status, "ZZTESTE", datetime.now(UTC), resolvido=resolvido)
        assert _item(db).forcar is True, status
        it = _item(db)
    _zera_agenda(db, ativo=False)


def test_fa4_forcar_limpo_so_depois_da_carga_criada_e_logado(db, origem):  # noqa: F811
    _amostras(db)
    _forcado(db)
    n = len(_logs_forcar(db))
    vistos = []

    def esperar(db, ex_id, parar=None):
        vistos.append(_item(db).forcar)  # durante a carga criada a partir do forcar, ele já foi consumido
        return _esperar(db, ex_id, parar)
    feitos = ag.ciclo(db, agora=_local(2026, 10, 30, 17, 28), leitores=_leitores(origem), esperar=esperar)
    assert feitos == [("saude", "sucesso")] and vistos == [False]
    it = _item(db)
    assert it.forcar is False and db.get(Execucao, it.ultima_execucao_id) is not None
    novos = _logs_forcar(db)[n:]
    assert len(novos) == 1 and "antes=True depois=False origem=agenda" in novos[0]
    assert f"execucao_id={it.ultima_execucao_id}" in novos[0] and "@" not in novos[0]
    _zera_agenda(db, ativo=False)


# ---------------------------------------------------------------- FA1 / FA2 / FA3

@pytest.mark.agenda_real
def test_fa1_desligar_mantem_forcar_nao_roda_e_completa_409(client, tokens, db, origem):  # noqa: F811
    _amostras(db)
    _forcado(db)
    r = client.put(SAUDE, json={"ativo": False}, headers=tokens["admin"])
    assert r.status_code == 200
    it = _item(db)
    assert it.ativo is False and it.forcar is True
    assert ag.devido(it, datetime.now(UTC), 0, 1440) is False and ag.proxima(it, datetime.now(UTC), 0, 1440) is None
    assert ag.ciclo(db, agora=_local(2026, 10, 30, 3, 0), leitores=_leitores(origem), esperar=_esperar) == []
    r = client.post("/api/conectores/saude/completa", headers=tokens["admin"])
    assert r.status_code == 409
    _zera_agenda(db, ativo=False)


def test_fa2_religar_sem_marcar_volta_false_com_marcar_volta_true(client, tokens, db):
    _forcado(db, ativo=False)
    assert client.put(SAUDE, json={"ativo": True}, headers=tokens["admin"]).status_code == 200
    assert _item(db).forcar is False  # padrão (CT26d)
    _forcado(db, ativo=False)
    r = client.put(SAUDE, json={"ativo": True, "forcar": True}, headers=tokens["admin"])
    assert r.status_code == 200, r.text
    it = _item(db)
    assert it.ativo is True and it.forcar is True
    assert client.put(SAUDE, json={"forcar": True}, headers=tokens["admin"]).status_code == 422  # só junto do ativo
    _zera_agenda(db, ativo=False)


def test_fa3_toda_mudanca_do_forcar_vai_para_controle_logs(client, tokens, db, monkeypatch):
    from app.core.config import get_settings
    monkeypatch.setenv("AGENDA_HABILITADA", "true")
    get_settings.cache_clear()
    _zera_agenda(db, ativo=False)
    db.execute(update(Agendamento).where(Agendamento.conector == "saude").values(ativo=True))
    db.commit()
    n = len(_logs_forcar(db))
    assert client.post(f"{SAUDE}/executar-agora", headers=tokens["admin"]).status_code == 202   # tela: F->T
    assert client.put(SAUDE, json={"forcar": False}, headers=tokens["admin"]).status_code == 200  # tela: T->F
    novos = _logs_forcar(db)[n:]
    assert any("antes=False depois=True origem=tela" in m for m in novos)
    assert any("antes=True depois=False origem=tela" in m for m in novos)
    assert all("quem=usuario_id=" in m and "@" not in m for m in novos)
    criado = db.scalar(select(Log.criado_em).where(Log.mensagem == novos[-1]))
    assert criado is not None
    _zera_agenda(db, ativo=False)
    get_settings.cache_clear()


# ---------------------------------------------------------------- SL1

def test_sl1_amostra_respeita_o_teto(monkeypatch):
    monkeypatch.delenv("SIL_MAX_LOTES_POR_EXECUCAO", raising=False)
    api = Api(ccms=range(1, 5001))
    con = _con(api, max_lotes=3)
    linhas = con.ler_amostra("Empresas")
    assert len(linhas) == 150 and api.sequencias == [1, 51, 101]
    assert con.ultima_parcial and con.ultima_parcial["ha_mais"] is True


def test_sl1_completa_passa_de_50_lotes_sem_a_env(monkeypatch):
    monkeypatch.delenv("SIL_MAX_LOTES_POR_EXECUCAO", raising=False)
    from app.connectors.sil.config import ConfigSil
    assert ConfigSil.do_ambiente().max_lotes_por_execucao == 1  # teto padrão continua 1 (amostra)
    api = Api(ccms=range(1, 60 * 50 + 1))  # 60 lotes cheios
    con = _con(api)  # teto 1
    s = con.abrir()
    try:
        linhas, ultima = [], None
        while True:
            pg = con.ler_pagina(s, "Empresas", None, ("Ccm",), ultima, 1000)
            if not pg:
                break
            linhas += pg
            ultima = (pg[-1]["Ccm"],)
    finally:
        s.close()
    assert len(linhas) == 3000 and s.lotes > 50 and not s.ha_mais
