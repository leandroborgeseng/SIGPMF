"""Medição por etapa da prata (bronze -> prata) com bronze SINTÉTICA ZZTESTE (padrão 500 mil linhas).

Uso (só banco de TESTE, nome terminando em _test):
    TEST_DATABASE_URL=postgresql+psycopg://.../sigpmf_prata_perf_test python -m tests.bench_prata 500000 [--json saida.json]
Imprime a tabela etapa / segundos / % do total / RSS atual / RSS pico. Nenhum dado real, nenhum valor de registro."""
import json
import os
import sys
import time

if __name__ == "__main__":
    url = os.environ["TEST_DATABASE_URL"]
    assert url.rsplit("/", 1)[-1].endswith("_test"), "só banco *_test"
    os.environ["DATABASE_URL"] = url
    os.environ.setdefault("JWT_SECRET", "segredo-de-benchmark-somente-teste")
    n = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 500000
    from sqlalchemy import func, select

    from app.core.db import SessionLocal
    from app.models.camadas import RegistroBronze
    from app.silver import processamento
    from tests.bench_ouro import carrega
    db = SessionLocal()
    if (db.scalar(select(func.count()).select_from(RegistroBronze)) or 0) < n:
        t = time.monotonic()
        carrega(url, n)
        print(f"carga bronze sintética {n}: {time.monotonic() - t:.1f}s", flush=True)
    med = processamento.Medicao()
    s = processamento.reconstruir(db, medicao=med)
    db.commit()
    med.marca("commit")
    total = med.total()
    print(f"{'etapa':34} {'seg':>8} {'%':>6} {'rss_mb':>8} {'pico_mb':>8}  entrada/saida")
    for e in med.etapas:
        print(f"{e['etapa']:34} {e['segundos']:8.2f} {100 * e['segundos'] / total:6.1f} {e['rss_mb']:8.0f} "
              f"{e['rss_pico_mb']:8.0f}  {e['linhas_entrada']}/{e['linhas_saida']}")
    print(f"{'TOTAL':34} {total:8.2f}  {s}")
    if "--json" in sys.argv:
        with open(sys.argv[sys.argv.index("--json") + 1], "w") as f:
            json.dump({"n": n, "total": total, "etapas": med.etapas, "resumo": s}, f, indent=1)
    db.close()
