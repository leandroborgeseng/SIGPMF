"""TL 27/10: a amostra responde 202 com o id da execução e roda em background (status + sinal de vida), respeitando a
trava do #20. O proxy cortava o POST quando prata/ouro passavam do timeout e o ouro nunca terminava.
Só dados sintéticos (OrigemMock / ZZTESTE); a thread real é usada aqui (o conftest deixa os outros testes síncronos)."""
import time

import pytest
from sqlalchemy import select

from app.gold import visao
from app.models.controle import Auditoria, Execucao
from app.services import carga_conector as cc
from app.services import trava as tv
from tests.test_zz_carga_conector import leitor  # noqa: F401 - fixture (origem sintética + env do SQL Server)

DISPARO_REAL = cc.disparar_amostra  # capturado antes do patch síncrono do conftest
TIMEOUT_REQUISICAO_S = 1.0  # o "proxy" do teste: o POST tem de voltar bem antes disso
OURO_LENTO_S = 3.0  # ouro mais lento que o timeout da requisição


@pytest.fixture
def thread_real(monkeypatch):
    monkeypatch.setattr(cc, "disparar_amostra", DISPARO_REAL)


@pytest.fixture
def ouro_lento(monkeypatch):
    original = visao.reconstruir
    estado = {"falhar": False}

    def lento(db, progresso=None):
        passos = 6
        for i in range(passos):  # sinal de vida durante o ouro, como o reconstruir real a cada lote
            time.sleep(OURO_LENTO_S / passos)
            if progresso:
                progresso(i + 1, passos)
        if estado["falhar"]:
            raise RuntimeError("ZZTESTE falha sintética no ouro")
        return original(db, progresso=progresso)
    monkeypatch.setattr(visao, "reconstruir", lento)
    return estado


def _espera_fim(client, tokens, eid, limite_s=60.0):
    vistos, t0 = [], time.monotonic()
    while time.monotonic() - t0 < limite_s:
        s = client.get(f"/api/conectores/saude/amostra/{eid}", headers=tokens["admin"])
        assert s.status_code == 200, s.text
        j = s.json()
        vistos.append(j)
        if not j["em_andamento"]:
            return j, vistos
        time.sleep(0.2)
    raise AssertionError(f"amostra {eid} não terminou em {limite_s}s: {vistos[-1]}")


def test_ouro_mais_lento_que_o_timeout_responde_202_rapido_e_conclui(client, tokens, db, leitor, thread_real,  # noqa: F811
                                                                    ouro_lento):
    t0 = time.monotonic()
    r = client.post("/api/conectores/saude/amostra", headers=tokens["admin"])
    dt = time.monotonic() - t0
    assert r.status_code == 202, r.text
    assert dt < TIMEOUT_REQUISICAO_S, f"POST levou {dt:.2f}s (ouro roda {OURO_LENTO_S}s)"
    j = r.json()
    assert j["em_andamento"] is True and j["status"] == "em_andamento" and j["id"]
    eid = j["id"]

    # #20: enquanto a thread roda, a trava do sistema está ocupada -> nova amostra ou completa = 409, nada é interrompido
    assert not tv.trava_livre(db, "SAUDE")
    r2 = client.post("/api/conectores/saude/amostra", headers=tokens["admin"])
    assert r2.status_code == 409 and "amostra de Saúde em andamento" in r2.json()["detail"]
    assert client.get("/api/conectores/saude/bloqueio", headers=tokens["admin"]).json()["bloqueio"]["viva"] is True

    fim, vistos = _espera_fim(client, tokens, eid)
    assert fim["status"] in ("sucesso", "sem_novidades"), fim
    assert fim["etapa"] == "concluida" and fim["finalizado_em"] and fim["lidos"] > 0
    assert "ouro" in {v["etapa"] for v in vistos}, "a tela tem de ver a etapa do ouro com contador"
    batidas = {v["atualizado_em"] for v in vistos if v["etapa"] == "ouro"}
    assert len(batidas) >= 2, "sinal de vida tem de avançar durante o ouro"
    assert tv.trava_livre(db, "SAUDE"), "a thread solta a trava no fim"
    db.expire_all()
    acoes = {a.acao for a in db.scalars(select(Auditoria).where(Auditoria.alvo == f"execucao:{eid}"))}
    assert {"amostra_conector_iniciada", "amostra_conector_executada"} <= acoes


def test_falha_no_ouro_em_background_vira_falha_e_solta_a_trava(client, tokens, db, leitor, thread_real,  # noqa: F811
                                                               ouro_lento):
    ouro_lento["falhar"] = True
    r = client.post("/api/conectores/saude/amostra", headers=tokens["admin"])
    assert r.status_code == 202
    fim, _ = _espera_fim(client, tokens, r.json()["id"])
    assert fim["status"] == "falha" and fim["finalizado_em"] and "ZZTESTE" not in (fim["mensagem"] or "")
    assert tv.trava_livre(db, "SAUDE")
    db.expire_all()
    assert db.get(Execucao, r.json()["id"]).status == "falha"


def test_status_da_amostra_404_para_outro_conector_ou_tipo(client, tokens, db, leitor):  # noqa: F811
    r = client.post("/api/conectores/saude/amostra", headers=tokens["admin"])
    eid = r.json()["id"]
    assert client.get(f"/api/conectores/cemiterio/amostra/{eid}", headers=tokens["admin"]).status_code == 404
    assert client.get("/api/conectores/saude/amostra/999999", headers=tokens["admin"]).status_code == 404
    assert client.get(f"/api/conectores/saude/amostra/{eid}", headers=tokens["operador"]).status_code == 403


def test_reconstrucoes_de_conectores_diferentes_sao_serializadas(db):
    """Prata/ouro são globais: a trava de transação das camadas impede duas reconstruções ao mesmo tempo
    (amostra de Saúde x amostra/completa de Cemitério, que têm travas #20 diferentes)."""
    from app.core.db import SessionLocal
    outra = SessionLocal()
    try:
        tv.aguarda_camadas(outra)  # segura (transação aberta)
        with pytest.raises(TimeoutError):
            tv.aguarda_camadas(db, espera_s=0.1, timeout_s=0.5)
        db.rollback()
        outra.commit()  # some no commit
        tv.aguarda_camadas(db, espera_s=0.1, timeout_s=2)
        db.rollback()
    finally:
        outra.close()
