"""PR-B QA D1: sinal de vida a cada INTERVALO_S durante a LEITURA da origem (amostra e carga completa).
Origem sintética (ZZTESTE / fixture saude+cemiterio) que dorme mais que o intervalo; banco *_test."""
import time

from app.services import carga_conector as cc  # noqa: I001
from app.services import subetapa as sub
from tests.test_zz_carga_conector import Leitor
from tests.test_zzz_carga_completa import _amostra, origem  # noqa: F401  (fixture sintética)


class LeitorLento(Leitor):
    def ler_amostra(self, tabela, politica=None):
        time.sleep(0.35)  # origem lenta: mais que o intervalo do pulso
        return super().ler_amostra(tabela, politica=politica)


def _espia(monkeypatch):
    chamadas = []
    original = sub.sinal
    monkeypatch.setattr(sub, "INTERVALO_S", 0.1)
    monkeypatch.setattr(sub, "sinal", lambda ex_id, *a, **k: (chamadas.append((time.monotonic(), ex_id)),
                                                               original(ex_id, *a, **k)))
    return chamadas


def test_d1_amostra_pulso_durante_leitura_lenta(db, monkeypatch):
    chamadas = _espia(monkeypatch)
    t0 = time.monotonic()
    ex = cc.executar_amostra(db, "admin@aion.local", "cemiterio", leitor=LeitorLento("CEMITERIO"))
    assert ex.status in ("sucesso", "sem_novidades"), ex.mensagem
    leitura = [t for t, i in chamadas if i == ex.id and t - t0 < 0.3]
    assert len(leitura) >= 2  # o pulso bateu enquanto a 1ª tabela ainda estava sendo lida
    db.refresh(ex)
    assert ex.ultimo_sinal_em is not None


def test_d1_carga_completa_pulso_durante_leitura_lenta(client, tokens, origem, db, monkeypatch):  # noqa: F811
    _amostra(db, "saude")
    o = origem["SAUDE"]
    pagina = o.ler_pagina

    def lenta(*a, **k):
        time.sleep(0.25)
        return pagina(*a, **k)
    monkeypatch.setattr(o, "ler_pagina", lenta)
    chamadas = _espia(monkeypatch)
    r = client.post("/api/conectores/saude/completa", headers=tokens["admin"])
    assert r.status_code in (200, 202), r.text
    ex_id = r.json().get("execucao_id") or r.json().get("id")
    assert any(i == ex_id for _, i in chamadas) if ex_id else len(chamadas) >= 2
    assert len(chamadas) >= 2
