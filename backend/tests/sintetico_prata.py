"""Gerador de bronze SINTÉTICA para a prata (sem CPF, nomes/nascimentos/mães colidindo) e snapshot comparável.
Só dados inventados; usado pelo teste de regressão e pelo benchmark (python -m tests.sintetico_prata N)."""
import hashlib
import random
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select

from app.models.camadas import Cidadao, CorrelacaoPendente, Inconsistencia, RegistroBronze, Vinculo
from app.models.controle import Execucao

NOMES = ["ANA", "BRUNO", "CARLA", "DIEGO", "ELAINE", "FABIO", "GISELE", "HUGO", "IARA", "JOAO", "KELLY", "LUCAS",
         "MARIA", "NUNO", "OLGA", "PAULO", "QUITERIA", "RAFAEL", "SILVIA", "TIAGO"]
SOBRENOMES = ["SILVA", "SOUZA", "COSTA", "LIMA", "ALVES", "ROCHA", "DIAS", "MOURA", "PINTO", "TEIXEIRA", "FREITAS",
              "BORGES", "CUNHA", "MELO", "RAMOS"]


def gera(db, n: int, semente: int = 7) -> int:
    rnd = random.Random(semente)
    ex = Execucao(tipo="sintetico_prata", sistema="SAUDE", status="sucesso", usuario="teste")
    db.add(ex)
    db.flush()
    base = date(1950, 1, 1)
    linhas = []
    for i in range(n):
        if i and rnd.random() < 0.08:  # cópia exata de alguém (nome+nasc+mãe): vai para revisão
            d = dict(linhas[rnd.randrange(len(linhas))].dados)
            d["Matricula"] = f"SIN{i:07d}"
        else:
            d = {"Matricula": f"SIN{i:07d}",
                 "Nome": f"{rnd.choice(NOMES)} {rnd.choice(SOBRENOMES)}" + (" " if rnd.random() < 0.1 else ""),
                 "Dt_Nasc": (base + timedelta(days=rnd.randrange(3000))).isoformat() if rnd.random() > 0.05 else None,
                 "Mae": f"{rnd.choice(NOMES)} {rnd.choice(SOBRENOMES)}" if rnd.random() > 0.1 else "",
                 "Cpf": None, "Cartao_Sus": None, "Sexo": rnd.choice("MF")}
        h = hashlib.sha256(repr(sorted(d.items())).encode()).hexdigest()
        linhas.append(RegistroBronze(sistema_origem="SAUDE", tabela="pacientes", id_origem=d["Matricula"], dados=d, hash=h,
                                     execucao_id=ex.id, atualizado_origem_em=datetime(2026, 1, 1, tzinfo=UTC)))
    db.add_all(linhas)
    db.flush()
    return ex.id


def snapshot(db) -> dict:
    return {
        "vinculos": sorted((v.sistema, v.tabela, v.chave_origem, v.cidadao_id, v.metodo, float(v.confianca or 0),
                            tuple(v.atributos or ())) for v in db.scalars(select(Vinculo))),
        "pendentes": sorted((p.sistema, p.tabela, p.chave_origem, p.motivo, tuple(p.candidatos or ()), repr(p.detalhe))
                            for p in db.scalars(select(CorrelacaoPendente))),
        "inconsistencias": sorted((i.sistema, i.tabela, i.chave_origem, i.tipo, i.campo or "", i.cidadao_id or "")
                                  for i in db.scalars(select(Inconsistencia))),
        "cidadaos": sorted((c.cidadao_id, c.nome, bool(c.falecido)) for c in db.scalars(select(Cidadao))),
    }


if __name__ == "__main__":  # benchmark: TEST_DATABASE_URL obrigatório (banco de teste, nunca produção)
    import os
    import sys
    import time
    os.environ["DATABASE_URL"] = os.environ["TEST_DATABASE_URL"]
    os.environ.setdefault("JWT_SECRET", "segredo-de-benchmark-somente-teste")
    from app.core.db import SessionLocal
    from app.silver import processamento
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 10000
    db = SessionLocal()
    try:
        gera(db, n)
        res = {}
        modos = (True,) if "--so-indexado" in sys.argv else (False, True)
        for indexado in modos:
            t = time.monotonic()
            processamento.reconstruir(db, indexado=indexado)
            res[indexado] = (time.monotonic() - t, snapshot(db))
        if False in res:
            print(f"N={n} linear={res[False][0]:.1f}s indexado={res[True][0]:.1f}s igual={res[False][1] == res[True][1]}")
        else:
            print(f"N={n} indexado={res[True][0]:.1f}s")
    finally:
        db.rollback()
        db.close()
