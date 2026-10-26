"""CLI: python -m app.demo [--completa] [--conjunto base|saude|cemiterio|saude+cemiterio]"""
import sys

from app.core.db import SessionLocal
from app.services.pipeline import executar_demo

if __name__ == "__main__":
    db = SessionLocal()
    conj = sys.argv[sys.argv.index("--conjunto") + 1] if "--conjunto" in sys.argv else "base"
    ex = executar_demo(db, "cli", completa="--completa" in sys.argv, conjunto=conj)
    print(f"execucao={ex.id} status={ex.status} lidos={ex.lidos} novos={ex.novos} "
          f"atualizados={ex.atualizados} ignorados={ex.ignorados} lotes={ex.lotes}")
    sys.exit(0 if ex.status != "falha" else 1)
