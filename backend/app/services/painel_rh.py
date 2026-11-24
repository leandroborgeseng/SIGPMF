"""Painel de RH (RH2): agregados da folha, direto de silver.rh_remuneracao. Nenhuma linha por pessoa sai daqui.

- Grupos com menos de MINIMO servidores não saem (evita identificar alguém por recorte pequeno); filtro que deixa menos
  que isso devolve `suprimido`. A consulta por pessoa continua sendo a do portal público.
- "Pessoas pagas" = vencimentos > 0: até 2015 o portal lista também matrículas sem pagamento no mês, que inflariam o quadro
  e derrubariam a média. As linhas zeradas seguem na prata, mas ficam fora de todos os números daqui.
- Mês padrão: o último mês comum com dados. O 13º (mês 13) é consultado à parte e fica fora da série mensal.
- Secretaria: nome padronizado (app/silver/rh.py). Grupo: ativa (secretarias), inativos (aposentados e pensionistas),
  outros (cedidos e indenizados).
"""
from datetime import date

from sqlalchemy import case, distinct, extract, func, select
from sqlalchemy.orm import Session

from app.models.rh import MesRh
from app.models.rh import Remuneracao as R

MINIMO = 5
GRUPOS = ("ativa", "inativos", "outros")
MES_13 = 13
FAIXAS = ((3000, "até R$ 3 mil"), (5000, "R$ 3 a 5 mil"), (8000, "R$ 5 a 8 mil"), (12000, "R$ 8 a 12 mil"),
          (20000, "R$ 12 a 20 mil"), (None, "acima de R$ 20 mil"))
TEMPOS = ((5, "até 5 anos"), (10, "5 a 10 anos"), (20, "10 a 20 anos"), (30, "20 a 30 anos"), (None, "mais de 30 anos"))


def _f(v) -> float:
    return float(v or 0)


def _pontos(linhas) -> list[dict]:
    """(dim, valor, servidores): só grupos com pelo menos MINIMO servidores."""
    return [{"periodo": "", "dim": d or "Não informado", "valor": _f(v)} for d, v, n in linhas if n >= MINIMO]


def _totais(db: Session, f: list) -> dict:
    n, bruto, desc, liq, med, afast = db.execute(select(
        func.count(), func.sum(R.vencimentos), func.sum(R.descontos), func.sum(R.liquido),
        func.percentile_cont(0.5).within_group(R.vencimentos), func.count().filter(R.situacao == "Afastado")).where(*f)).one()
    if n < MINIMO:
        return {"servidores": n, "suprimido": True}
    return {"servidores": n, "bruto": _f(bruto), "descontos": _f(desc), "liquido": _f(liq), "media": _f(bruto) / n, "mediana": _f(med),
            "afastados": afast, "suprimido": False}


def disponivel(db: Session) -> dict:
    meses = sorted((a, m) for a, m in db.execute(select(MesRh.ano, MesRh.mes).where(MesRh.linhas > 0)))
    return {"meses": [{"ano": a, "mes": m} for a, m in meses],
            "secretarias": [s for s, in db.execute(select(R.secretaria).distinct().order_by(R.secretaria))],
            "carregado_em": db.scalar(select(func.max(MesRh.carregado_em)))}


def painel(db: Session, ano: int | None = None, mes: int | None = None, secretaria: str | None = None, grupo: str | None = None) -> dict:
    disp = disponivel(db)
    # o portal não publicou o 13º de todos os anos (faltam 2018 e 2019): o total anual desses anos fica sem ele
    anos_mes = {x["ano"] for x in disp["meses"]}
    disp["anos_sem_13"] = sorted((anos_mes - {max(anos_mes, default=0)}) - {x["ano"] for x in disp["meses"] if x["mes"] == MES_13})  # o ano corrente ainda não fechou
    comuns = [(x["ano"], x["mes"]) for x in disp["meses"] if x["mes"] != MES_13]
    if not comuns:
        return {"disponivel": disp, "filtro": None, "atual": None}
    tem = {(x["ano"], x["mes"]) for x in disp["meses"]}
    if ano is None or mes is None or (ano, mes) not in tem:
        ano, mes = comuns[-1]
    grupo = grupo if grupo in GRUPOS else None
    pagos = R.vencimentos > 0
    base = [pagos, R.secretaria == secretaria] if secretaria else [pagos]
    if grupo:
        base.append(R.grupo == grupo)
    sem_sec = [pagos, R.grupo == grupo] if grupo else [pagos]  # por secretaria: mostra todas, para o clique trocar de uma para outra
    periodo = [R.ano == ano, R.mes == mes]
    n = func.count()

    def por(col, *filtros, valor=func.sum(R.vencimentos), limite=None, ordem=None):
        q = select(col, valor, n).where(*filtros).group_by(col).having(n >= MINIMO).order_by((ordem if ordem is not None else valor).desc())
        return _pontos(db.execute(q.limit(limite) if limite else q))

    def faixas(col_valor, limites):
        whens = [(col_valor < lim, rot) for lim, rot in limites if lim is not None]
        rotulo = case(*whens, else_=limites[-1][1])
        linhas = {r: c for r, c in db.execute(select(rotulo, n).where(*periodo, *base).group_by(rotulo))}
        return [{"periodo": "", "dim": rot, "valor": float(linhas[rot])} for _, rot in limites if linhas.get(rot, 0) >= MINIMO]
    fim_mes = date(ano, min(mes, 12), 28)
    anos_casa = (fim_mes - R.admissao) / 365.25

    # série mensal e anual: o filtro vale, o período não (13º fora da mensal, dentro do total do ano)
    mensal = list(db.execute(select(R.ano, R.mes, func.sum(R.vencimentos), n).where(*base, R.mes != MES_13).group_by(R.ano, R.mes)))
    anual = list(db.execute(select(R.ano, func.sum(R.vencimentos)).where(*base).group_by(R.ano)))
    quadro = {a: v for a, v in db.execute(select(R.ano, func.count() / func.count(distinct(R.mes))).where(*base, R.mes != MES_13).group_by(R.ano))}  # média mensal
    adm = {int(a): c for a, c in db.execute(select(extract("year", R.admissao), func.count(distinct(R.matricula)))
                                            .where(*base, R.admissao.is_not(None)).group_by(extract("year", R.admissao)))}
    dem = {int(a): c for a, c in db.execute(select(extract("year", R.demissao), func.count(distinct(R.matricula)))
                                            .where(*base, R.demissao.is_not(None)).group_by(extract("year", R.demissao)))}
    anos = sorted(quadro)
    serie = lambda pares: [{"periodo": p, "dim": "", "valor": float(v)} for p, v in pares]  # noqa: E731
    atual = _totais(db, [*periodo, *base])
    anterior = _totais(db, [R.ano == ano - 1, R.mes == mes, *base]) if (ano - 1, mes) in tem else None
    if mes != MES_13 and not atual["suprimido"]:
        atual["admissoes"] = db.scalar(select(func.count()).where(*periodo, *base, extract("year", R.admissao) == ano, extract("month", R.admissao) == mes))
        atual["desligamentos"] = db.scalar(select(func.count()).where(*periodo, *base, extract("year", R.demissao) == ano, extract("month", R.demissao) == mes))
    return {
        "disponivel": disp, "filtro": {"ano": ano, "mes": mes, "secretaria": secretaria, "grupo": grupo},
        "atual": atual, "anterior": anterior if anterior and not anterior["suprimido"] else None,
        "mensal": {"bruto": serie((f"{a}-{m:02d}", v) for a, m, v, c in mensal if c >= MINIMO),
                   "servidores": serie((f"{a}-{m:02d}", c) for a, m, v, c in mensal if c >= MINIMO)},
        "anual": {"bruto": serie((str(a), v) for a, v in anual if quadro.get(a, 0) >= MINIMO),
                  "servidores": serie((str(a), quadro[a]) for a in anos if quadro[a] >= MINIMO),
                  # admissão e desligamento sem supressão por ano só com quantidades >= MINIMO
                  "admissoes": serie((str(a), adm.get(a, 0)) for a in anos if adm.get(a, 0) >= MINIMO),
                  "desligamentos": serie((str(a), dem.get(a, 0)) for a in anos if dem.get(a, 0) >= MINIMO)},
        "por_secretaria_bruto": por(R.secretaria, *periodo, *sem_sec),
        "por_secretaria_servidores": por(R.secretaria, *periodo, *sem_sec, valor=n),
        "por_cargo_servidores": por(R.cargo, *periodo, *base, valor=n, limite=15),
        "por_cargo_media": por(R.cargo, *periodo, *base, valor=func.avg(R.vencimentos), limite=15),
        "por_situacao": por(R.situacao, *periodo, *base, valor=n),
        "por_faixa": faixas(R.vencimentos, FAIXAS),
        "por_tempo": faixas(anos_casa, TEMPOS),
    }
