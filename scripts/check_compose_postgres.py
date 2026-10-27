#!/usr/bin/env python3
"""Falha se algum serviço Postgres dos compose de produção publicar porta no host.

Produção (Coolify) = todo docker-compose*.yml exceto *.override.yml (uso local/CI).
Usa `docker compose -f ARQ config --format json` (arquivo já resolvido, sem override).
Regra: serviço com imagem postgres -> sem `ports`, com `expose` contendo 5432.
"""
import glob
import json
import os
import subprocess
import sys

raiz = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
arquivos = sorted(
    f for f in glob.glob(os.path.join(raiz, "docker-compose*.y*ml")) if ".override." not in os.path.basename(f)
)
if not arquivos:
    sys.exit("nenhum docker-compose de produção encontrado")

env = dict(os.environ)
for k in ("POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD"):
    env.setdefault(k, "ci_check")
erros = []
for arq in arquivos:
    nome = os.path.basename(arq)
    saida = subprocess.run(
        ["docker", "compose", "-f", arq, "--project-directory", raiz, "config", "--format", "json", "--no-interpolate"],
        capture_output=True, text=True, env=env, check=True,
    ).stdout
    servicos = json.loads(saida).get("services", {})
    pgs = {n: s for n, s in servicos.items() if "postgres" in str(s.get("image", "")) or n in ("postgres", "db")}
    for n, s in pgs.items():
        if s.get("ports"):
            erros.append(f"{nome}: serviço {n} publica portas {s['ports']}")
        expose = [str(e) for e in s.get("expose") or []]
        if not any(e.split("/")[0] == "5432" for e in expose):
            erros.append(f"{nome}: serviço {n} sem expose 5432 (expose={expose})")
    print(f"{nome}: postgres={sorted(pgs)} ok={not any(e.startswith(nome) for e in erros)}")
if erros:
    print("\n".join(erros))
    sys.exit(1)
print("ok: nenhum Postgres publica porta nos compose de produção")
