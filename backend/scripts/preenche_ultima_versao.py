"""Parte 2 (0038): preenchimento inicial de bronze.ultima_versao, FORA do boot, em lotes de faixa de id.
Idempotente e retomável (a marca '_ultima_versao' em controle.camadas_marca guarda a última faixa confirmada).
Predicado D6: quarentena IS NULL AND ambiente_origem = 'producao'. Só ids e chaves: nada de dado pessoal no log.

  DATABASE_URL=... python scripts/preenche_ultima_versao.py [--lote 200000]
Sem rodar este script, a primeira prata/ouro depois do deploy faz o mesmo preenchimento (mais demorada)."""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--lote", type=int, default=200_000)
    a = p.parse_args()
    from app.core.db import SessionLocal
    from app.services import ultima_versao as uv
    db = SessionLocal()
    t = time.monotonic()
    try:
        ate = uv.sincroniza(db, lote=a.lote)
    finally:
        db.close()
    print(f"ultima_versao sincronizada até o id {ate} em {time.monotonic() - t:.1f}s")


if __name__ == "__main__":
    main()
