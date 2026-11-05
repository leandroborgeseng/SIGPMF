"""Dados SINTÉTICOS do painel modelo da folha (% horas extras). Claramente marcados como exemplo (`exemplo = true`).

A folha real ainda não tem carga (onda 1). Este gerador:
- só roda em banco com "_test" no nome (recusa qualquer outro), nunca no boot, nunca por agenda;
- grava gold.painel_fato (painel "folha-modelo") e uma linha em controle.execucoes (sistema EXEMPLO_FOLHA), que é de
  onde o selo "Fonte · carga de" lê a data (P03);
- tem um mês sem carga (lacuna, P02) e grupos com menos de 5 servidores (supressão k, K01/K04).
Nenhuma pessoa: só agregados inventados, órgãos com prefixo ZZTESTE.

Uso: python -m app.paineis.exemplo   (lê DATABASE_URL; recusa se o nome do banco não tiver "_test")
"""
import os
import sys

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

PAINEL = "folha-modelo"
SISTEMA = "EXEMPLO_FOLHA"
FIM = "2026-10"
MESES = 25
MES_SEM_CARGA = "2026-06"
# (secretaria, órgãos [(nome, servidores)]) — números inventados
ESTRUTURA = (
    ("SAUDE", (("ZZTESTE UBS Centro", 42), ("ZZTESTE Pronto-socorro Norte", 88), ("ZZTESTE Vigilância", 3), ("ZZTESTE Ouvidoria da Saúde", 3))),
    ("EDUCACAO", (("ZZTESTE EMEF Jardim", 61), ("ZZTESTE Creche Vila Nova", 27))),
    ("FAZENDA", (("ZZTESTE Tributação", 19), ("ZZTESTE Tesouraria", 8))),
    ("OBRAS", (("ZZTESTE Zeladoria", 54), ("ZZTESTE Frota", 12))),
    ("ADMINISTRACAO", (("ZZTESTE Protocolo", 4),)),
    ("CULTURA", (("ZZTESTE Biblioteca", 3),)),
)
RUBRICAS = (("ZZTESTE HE 50%", 0.62), ("ZZTESTE HE 100%", 0.30), ("ZZTESTE Adicional noturno", 0.08))


class BancoNaoTeste(RuntimeError):
    pass


def exige_banco_teste(db: Session) -> None:
    nome = db.scalar(text("SELECT current_database()")) or ""
    if "_test" not in nome:
        raise BancoNaoTeste(f"Recusado: o banco {nome!r} não tem '_test' no nome (dados de exemplo só em banco de teste).")


def _comp(i: int) -> str:
    a, m = (int(x) for x in FIM.split("-"))
    t = a * 12 + m - 1 - i
    return f"{t // 12:04d}-{t % 12 + 1:02d}"


def gerar(db: Session, *, sem_execucao: bool = False) -> dict:
    """Idempotente: apaga só as linhas do painel de exemplo (exemplo = true) antes de regravar."""
    exige_banco_teste(db)
    db.execute(text("DELETE FROM gold.painel_fato WHERE painel = :p AND exemplo"), {"p": PAINEL})
    eid = None
    if not sem_execucao:
        eid = db.scalar(text("INSERT INTO controle.execucoes (tipo, sistema, status, usuario, finalizado_em, lidos, novos, mensagem) "
                             "VALUES ('painel_exemplo', :s, 'sucesso', 'zzteste@aion.local', now(), 0, 0, 'ZZTESTE dados sintéticos') RETURNING id"),
                        {"s": SISTEMA})
    linhas = 0
    for i in range(MESES):
        comp = _comp(i)
        if comp == MES_SEM_CARGA:
            continue
        mes = int(comp[5:])
        sazonal = 1.0 + (0.35 if mes in (12, 1) else 0.12 if mes in (6, 7) else 0.0)
        cresc = 1.0 + 0.004 * (MESES - i)
        for si, (sec, orgaos) in enumerate(ESTRUTURA):
            for oi, (org, serv) in enumerate(orgaos):
                salario = 4200 + 650 * si + 120 * oi
                for ri, (rub, peso) in enumerate(RUBRICAS):
                    n = max(1, round(serv * (0.5, 0.3, 0.2)[ri]))
                    folha = round(n * salario * cresc, 2)
                    he = round(folha * (0.045 + 0.01 * si + 0.004 * oi) * sazonal * peso * 3.2, 2)
                    db.execute(text("INSERT INTO gold.painel_fato (painel, competencia, nivel1, nivel2, nivel3, m1, m2, n, exemplo, execucao_id) "
                                    "VALUES (:p, :c, :s, :o, :r, :m1, :m2, :n, true, :e)"),
                               {"p": PAINEL, "c": comp, "s": sec, "o": org, "r": rub, "m1": folha, "m2": he, "n": n, "e": eid})
                    linhas += 1
    db.commit()
    return {"linhas": linhas, "execucao_id": eid}


if __name__ == "__main__":  # pragma: no cover
    url = os.environ.get("DATABASE_URL") or ""
    if "_test" not in (make_url(url).database or ""):
        sys.exit("Recusado: DATABASE_URL precisa apontar para um banco com '_test' no nome.")
    from app.core.db import SessionLocal
    s = SessionLocal()
    try:
        print(gerar(s))
    finally:
        s.close()
