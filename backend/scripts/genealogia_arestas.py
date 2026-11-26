"""Popula gold.genealogia_arestas sem reconstruir o restante do ouro.

Também roda no fim da etapa normal do ouro (app.gold.visao.reconstruir).
Este comando só recalcula as arestas, para a produção não precisar refazer a gold inteira.

    docker compose exec -T backend python -m scripts.genealogia_arestas
"""
from __future__ import annotations


def main() -> int:
    from app.core.db import SessionLocal
    from app.genealogia.materializar import materializar

    with SessionLocal() as db:
        r = materializar(db)
        db.commit()
    print(
        f"OK: gold.genealogia_arestas geracao={r['geracao']} arestas={r['arestas']} "
        f"pessoas={r['pessoas']} segundos={r['segundos']} trgm={r['trgm']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
