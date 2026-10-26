"""Gera expected_counts.json a partir da fixture sintética (semente fixa SEED=20261025).

Uso: python tests/fixtures/gerar_fixture.py   (a partir de backend/)
Os registros em si (com CPFs sintéticos) não são gravados no repositório; são regerados em memória.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.ingestion.fixture_sintetica import SEED, contagens_esperadas, gerar  # noqa: E402

DESTINO = Path(__file__).with_name("expected_counts.json")


def calcula() -> dict:
    fx = gerar(SEED)
    return {"seed": SEED, "amostra_100": contagens_esperadas(fx, 100), "completa": contagens_esperadas(fx, None)}


if __name__ == "__main__":
    DESTINO.write_text(json.dumps(calcula(), indent=2, ensure_ascii=False) + "\n")
    print(DESTINO.read_text())
