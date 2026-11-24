"""S01: mede p95 dos endpoints de painel (shell) contra uma API LOCAL/HOMOLOGAÇÃO: aquecimento + 20 chamadas por endpoint.

Uso: SIGPMF_URL=http://127.0.0.1:18700 SIGPMF_USUARIO=... SIGPMF_SENHA=... python scripts/sla_paineis.py
Só leitura (GET). Não use contra produção sem OK do Tech Lead. Credenciais só por variável de ambiente, nunca no log.
"""
import os
import statistics
import sys
import time

import httpx

URL = os.environ.get("SIGPMF_URL", "http://127.0.0.1:18700").rstrip("/")
AQUECIMENTO, CHAMADAS, LIMITE_S = 5, 20, 2.0
CASOS = (
    ("folha-modelo (prefeitura)", "/api/paineis/folha-modelo", {}),
    ("folha-modelo competência 2026-07", "/api/paineis/folha-modelo", {"competencia": "2026-07"}),
    ("folha-modelo › Saúde", "/api/paineis/folha-modelo", {"secretaria": "SAUDE"}),
    ("folha-modelo › Saúde › órgão", "/api/paineis/folha-modelo", {"secretaria": "SAUDE", "caminho": "ZZTESTE UBS Centro"}),
)


def p95(xs: list[float]) -> float:
    s = sorted(xs)
    return s[max(0, int(round(0.95 * len(s))) - 1)]


def main() -> int:
    with httpx.Client(base_url=URL, timeout=30) as c:
        r = c.post("/api/auth/login", json={"usuario": os.environ["SIGPMF_USUARIO"], "senha": os.environ["SIGPMF_SENHA"]})
        r.raise_for_status()
        c.headers["Authorization"] = f"Bearer {r.json()['access_token']}"
        print(f"API: {URL} · aquecimento {AQUECIMENTO} · medição {CHAMADAS} chamadas por endpoint · limite p95 < {LIMITE_S:.0f} s\n")
        print("| Endpoint | n | mín (ms) | mediana (ms) | p95 (ms) | máx (ms) | OK |")
        print("|---|---|---|---|---|---|---|")
        falhou = False
        for nome, rota, prm in CASOS:
            for _ in range(AQUECIMENTO):
                c.get(rota, params=prm).raise_for_status()
            tempos = []
            for _ in range(CHAMADAS):
                t = time.perf_counter()
                c.get(rota, params=prm).raise_for_status()
                tempos.append(time.perf_counter() - t)
            ok = p95(tempos) < LIMITE_S
            falhou |= not ok
            ms = lambda v: f"{v * 1000:.1f}"  # noqa: E731
            print(f"| {nome} | {len(tempos)} | {ms(min(tempos))} | {ms(statistics.median(tempos))} | {ms(p95(tempos))} | {ms(max(tempos))} | {'sim' if ok else 'NÃO'} |")
    return 1 if falhou else 0


if __name__ == "__main__":
    sys.exit(main())
