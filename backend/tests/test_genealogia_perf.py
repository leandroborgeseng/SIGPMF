"""Volume sintético da árvore. Fora do CI: GENEALOGIA_PERF=1 pytest -q tests/test_genealogia_perf.py

200 mil cidadãos fictícios, cadeia de parentesco e homônimos. Mede o build das arestas e o
p50/p95 do endpoint em memória (montar), com meta de p95 abaixo de 1 s.
"""
import json
import os
import time

import pytest
from sqlalchemy import text

from app.genealogia.arvore import montar
from app.genealogia.materializar import materializar
from app.models.controle import Execucao

pytestmark = pytest.mark.skipif(
    os.environ.get("GENEALOGIA_PERF") != "1",
    reason="volume sintético; rode com GENEALOGIA_PERF=1",
)

N = 200_000
PASSO = 40_000
EGO = "zzperf-100000"


def test_vizinhanca_abaixo_de_um_segundo(db):
    try:
        db.add(Execucao(tipo="zzperf", sistema="ZZ", status="sucesso", usuario="zz@aion.local"))
        db.flush()
        ex = db.scalar(text("SELECT id FROM controle.execucoes WHERE tipo = 'zzperf' ORDER BY id DESC LIMIT 1"))
        db.execute(text("""
            INSERT INTO silver.cidadaos (cidadao_id, nome)
            SELECT 'zzperf-' || i, 'PESSOA PERF ' || i
              FROM generate_series(0, :n) s(i)
        """), {"n": N - 1})
        db.execute(text("""
            INSERT INTO silver.senior_dependentes
              (numemp, tipcol, numcad, coddep, nome, papel, grau, cidadao_id, cidadao_titular_id, sistema)
            SELECT 1, 1, i, 1, 'PESSOA PERF ' || (i - :passo), 'ascendente', 'mãe',
                   'zzperf-' || (i - :passo), 'zzperf-' || i, 'SENIOR'
              FROM generate_series(:passo, :ultimo) s(i)
        """), {"passo": PASSO, "ultimo": N - 1})
        db.execute(text("""
            UPDATE silver.cidadaos SET nome = 'HOMONIMO PERF DUPLICADO'
             WHERE cidadao_id IN ('zzperf-10', 'zzperf-11')
        """))
        db.execute(text("UPDATE silver.cidadaos SET nome = 'HELENA PERF UNICA' WHERE cidadao_id = 'zzperf-20'"))
        db.execute(text("""
            INSERT INTO silver.municipes
              (documento, tipo, tipo_pessoa, nome, nome_mae, cidadao_id, hash, execucao_id)
            SELECT lpad(i::text, 14, '9'), 'cidadao', 'F', 'FILHO PERF ' || i,
                   'PESSOA PERF ' || i, 'zzperf-' || (30000 + i), md5('zzperf' || i), :ex
              FROM generate_series(1000, 5999) s(i)
        """), {"ex": ex})
        db.execute(text("""
            INSERT INTO silver.municipes
              (documento, tipo, tipo_pessoa, nome, nome_mae, cidadao_id, hash, execucao_id)
            VALUES ('99999999999012', 'cidadao', 'F', 'FILHO HOMONIMO PERF', 'HOMONIMO PERF DUPLICADO',
                    'zzperf-12', 'zzperf-homonimo', :ex),
                   ('99999999999021', 'cidadao', 'F', 'FILHO HELENA PERF', 'HELENA PERF UNICA',
                    'zzperf-21', 'zzperf-helena', :ex)
        """), {"ex": ex})
        db.flush()
        t0 = time.perf_counter()
        resumo = materializar(db)
        build = time.perf_counter() - t0
        hom = montar(db, "zzperf-12")
        assert hom["ascendentes"] == []
        unica = montar(db, "zzperf-21")
        assert unica["ascendentes"] and unica["ascendentes"][0]["arestas"][0]["confianca"] == 0.70
        assert unica["ascendentes"][0]["pessoa"]["nome"] == "HELENA PERF UNICA"
        amostra = montar(db, EGO)
        assert [p["nivel"] for p in amostra["ascendentes"]] == [1, 2]
        assert [p["nivel"] for p in amostra["descendentes"]] == [1, 2]
        tempos = []
        for _ in range(21):
            t1 = time.perf_counter()
            montar(db, EGO)
            tempos.append(time.perf_counter() - t1)
        tempos = sorted(tempos[1:])
        p50 = tempos[len(tempos) // 2]
        p95 = tempos[max(0, int(len(tempos) * 0.95) - 1)]
        saida = {
            "cidadaos": N, "arestas": resumo["arestas"], "build_s": round(build, 3),
            "p50_s": round(p50, 4), "p95_s": round(p95, 4), "amostras": len(tempos),
            "trgm": resumo["trgm"],
        }
        with open("/tmp/genealogia-perf.json", "w", encoding="utf-8") as fh:
            json.dump(saida, fh)
        print("GENEALOGIA_PERF", saida)
        assert p95 < 1.0, saida
    finally:
        db.rollback()
