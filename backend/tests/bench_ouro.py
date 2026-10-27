"""Benchmark da prata + ouro com bronze SINTÉTICA (ZZTESTE) em volume (padrão 500 mil linhas).

Uso (só banco de TESTE, nome terminando em _test):
    TEST_DATABASE_URL=postgresql+psycopg://.../sigpmf_xxx_test python -m tests.bench_ouro 500000 [--so-prata | --so-ouro [--legado]]
Mede tempo e pico de memória (RSS) de processamento.reconstruir e visao.reconstruir. Nenhum dado real."""
import hashlib
import json
import os
import random
import resource
import sys
import time
from datetime import date, timedelta

NOMES = ["ANA", "BRUNO", "CARLA", "DIEGO", "ELAINE", "FABIO", "GISELE", "HUGO", "IARA", "JOAO", "KELLY", "LUCAS"]
SOBRENOMES = ["ZZTESTE SILVA", "ZZTESTE SOUZA", "ZZTESTE COSTA", "ZZTESTE LIMA", "ZZTESTE ALVES", "ZZTESTE ROCHA"]


def _cpf(n: int) -> str:
    from app.identity.cpf import digitos_verificadores
    b = f"{n:09d}"
    return b + digitos_verificadores(b)


def linhas(n: int, semente: int = 11):
    """~70% com CPF válido (10% deles repetidos em outra matrícula), 30% sem CPF; endereço e mãe preenchidos."""
    rnd = random.Random(semente)
    base = date(1940, 1, 1)
    for i in range(n):
        cpf = _cpf(100000000 + (rnd.randrange(i) if i and rnd.random() < 0.1 else i)) if rnd.random() < 0.7 else None
        d = {"Matricula": f"ZZT{i:08d}", "Nome": f"{rnd.choice(NOMES)} {rnd.choice(SOBRENOMES)} {i % 997}",
             "Dt_Nasc": (base + timedelta(days=rnd.randrange(25000))).isoformat(),
             "Mae": f"{rnd.choice(NOMES)} {rnd.choice(SOBRENOMES)}", "Cpf": cpf, "Cartao_Sus": None,
             "Sexo": rnd.choice("MF"), "Complemento": f"RUA ZZTESTE {i % 5000}", "Nr": str(i % 900), "Cep": "14400000",
             "dt_ultimaatual": "2026-01-01T00:00:00"}
        yield d


def carrega(url: str, n: int) -> None:
    import psycopg
    with psycopg.connect(url.replace("postgresql+psycopg", "postgresql")) as c:
        c.execute("insert into controle.execucoes (tipo, sistema, status, usuario) values "
                  "('bench_ouro', 'SAUDE', 'sucesso', 'zzteste') returning id")
        ex = c.execute("select max(id) from controle.execucoes").fetchone()[0]
        c.execute("insert into controle.sistemas (codigo, nome, dominio, descricao, ativo) values ('SAUDE','Saude ZZTESTE',"
                  "'saude','bench', true) on conflict do nothing")
        with c.cursor().copy("copy bronze.registros (sistema_origem, tabela, id_origem, dados, hash, execucao_id, "
                             "atualizado_origem_em) from stdin") as cp:
            for d in linhas(n):
                j = json.dumps(d)
                cp.write_row(("SAUDE", "pacientes", d["Matricula"], j, hashlib.sha256(j.encode()).hexdigest(), ex,
                              "2026-01-01T00:00:00+00"))
        c.execute("analyze bronze.registros")


def rss_mb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024


if __name__ == "__main__":
    url = os.environ["TEST_DATABASE_URL"]
    assert url.rsplit("/", 1)[-1].endswith("_test"), "só banco *_test"
    os.environ["DATABASE_URL"] = url
    os.environ.setdefault("JWT_SECRET", "segredo-de-benchmark-somente-teste")
    n = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 500000
    from sqlalchemy import func, select

    from app.core.db import SessionLocal
    from app.gold import visao
    from app.models.camadas import RegistroBronze
    from app.silver import processamento
    db = SessionLocal()
    if (db.scalar(select(func.count()).select_from(RegistroBronze)) or 0) < n:
        t = time.monotonic()
        carrega(url, n)
        print(f"carga bronze sintética {n}: {time.monotonic() - t:.1f}s", flush=True)
    so_ouro, legado = "--so-ouro" in sys.argv, "--legado" in sys.argv
    if not so_ouro:
        t = time.monotonic()
        s = processamento.reconstruir(db)
        print(f"prata: {time.monotonic() - t:.1f}s rss_max={rss_mb():.0f}MB {s}", flush=True)
        db.commit()
        if "--so-prata" in sys.argv:
            sys.exit(0)
    t = time.monotonic()
    g = (visao.reconstruir_legado if legado else visao.reconstruir)(db)
    print(f"ouro{' (legado)' if legado else ''}: {time.monotonic() - t:.1f}s rss_max={rss_mb():.0f}MB {g}", flush=True)
    t = time.monotonic()
    db.commit()
    print(f"commit: {time.monotonic() - t:.1f}s rss_max={rss_mb():.0f}MB", flush=True)
    db.close()
