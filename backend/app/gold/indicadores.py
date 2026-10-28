"""Ouro agregado para os painéis (onda 3, SA-F4): gold.indicadores (série, período, dimensão, valor). Sem pessoa.

Reconstruído no fim da prata, por agregação SQL (GROUP BY no Postgres, nada em memória por linha). Grupos com menos
de MINIMO_CELULA casos não saem nas séries por dimensão de saúde (evita reidentificação em recorte pequeno).
"""
from collections import defaultdict
from datetime import date

from sqlalchemy import delete, func, literal_column, select
from sqlalchemy.orm import Session

from app.models.camadas import (
    Agendamento,
    Atendimento,
    Cidadao,
    DividaAtiva,
    Imovel,
    Indicador,
    Lancamento,
    UnidadeSaude,
    Vacinacao,
)
from app.silver.fatos import NOMES_TRIBUTO, nomes

MINIMO_CELULA = 5
G1 = literal_column("1")  # GROUP BY pela 1ª coluna do SELECT (SQLAlchemy 2.1 não aceita o inteiro)
FAIXAS = ((1000, "até R$ 1 mil"), (10000, "R$ 1–10 mil"), (100000, "R$ 10–100 mil"), (None, "acima de R$ 100 mil"))


def _mes(col):
    return func.to_char(col, "YYYY-MM")


def reconstruir(db: Session) -> dict[str, int]:
    db.execute(delete(Indicador))
    out: list[Indicador] = []

    def add(serie, valor, periodo="", dim=""):
        out.append(Indicador(serie=serie, periodo=periodo or "", dim=(dim or "")[:200], valor=float(valor or 0)))

    hoje = date.today()
    uni = {u.codigo: u.nome or f"Unidade {u.codigo}" for u in db.scalars(select(UnidadeSaude))}
    dims = nomes(db)
    # ---- KPIs
    add("kpi", db.scalar(select(func.count()).select_from(Cidadao)), dim="cidadaos")
    add("kpi", db.scalar(select(func.count()).select_from(Imovel)), dim="imoveis")
    add("kpi", db.scalar(select(func.count()).select_from(Atendimento)), dim="atendimentos_12m")
    add("kpi", db.scalar(select(func.count()).select_from(Vacinacao)), dim="vacinas_24m")
    add("kpi", db.scalar(select(func.coalesce(func.sum(DividaAtiva.saldo), 0)).where(DividaAtiva.extinta.is_(False))), dim="divida_ativa")
    add("kpi", db.scalar(select(func.count(func.distinct(DividaAtiva.codi))).where(DividaAtiva.extinta.is_(False),
                                                                                 DividaAtiva.saldo > 0)), dim="devedores")
    add("kpi", db.scalar(select(func.count()).select_from(DividaAtiva).where(DividaAtiva.extinta.is_(False),
                                                                           DividaAtiva.protestos > 0)), dim="protestadas")
    ano = hoje.year
    lan_ano = select(func.coalesce(func.sum(Lancamento.valor), 0), func.coalesce(func.sum(Lancamento.valor_pago), 0)).where(
        Lancamento.exercicio == ano)
    lancado, pago = db.execute(lan_ano).one()
    add("kpi", lancado, dim="lancado_ano")
    add("kpi", pago, dim="arrecadado_ano")
    venc = db.execute(select(func.coalesce(func.sum(Lancamento.valor), 0),
                             func.coalesce(func.sum(Lancamento.valor).filter(Lancamento.pago.is_(False)), 0))
                      .where(Lancamento.vencimento < hoje)).one()
    add("kpi", round(100.0 * float(venc[1]) / float(venc[0]), 1) if venc[0] else 0, dim="inadimplencia_pct")
    esp = db.execute(select(func.avg(Agendamento.dias_espera))).scalar()
    add("kpi", round(float(esp), 1) if esp is not None else 0, dim="espera_media_dias")
    # ---- Saúde
    for p, n in db.execute(select(_mes(Atendimento.data), func.count()).where(Atendimento.data.is_not(None)).group_by(G1)):
        add("saude_atend_mes", n, periodo=p)
    for u, n in db.execute(select(Atendimento.unidade, func.count()).group_by(G1)):
        if n >= MINIMO_CELULA:
            add("saude_atend_unidade", n, dim=uni.get(u, f"Unidade {u}"))
    for p, n in db.execute(select(_mes(Vacinacao.data), func.count()).where(Vacinacao.data.is_not(None)).group_by(G1)):
        add("saude_vacina_mes", n, periodo=p)
    for v, n in db.execute(select(Vacinacao.vacina, func.count()).group_by(G1)):
        if n >= MINIMO_CELULA:
            add("saude_vacina_tipo", n, dim=v)
    for e, n, media in db.execute(select(Agendamento.especialidade, func.count(), func.avg(Agendamento.dias_espera)).group_by(G1)):
        if n >= MINIMO_CELULA:
            nome = dims["especialidades"].get(e) or f"Especialidade {e}"
            add("saude_agend_especialidade", n, dim=nome)
            add("saude_espera_especialidade", round(float(media), 1) if media is not None else 0, dim=nome)
    # ---- Fazenda
    for p, lan, pg in db.execute(select(_mes(Lancamento.vencimento), func.sum(Lancamento.valor), func.sum(Lancamento.valor_pago))
                                 .where(Lancamento.vencimento.is_not(None)).group_by(G1)):
        add("trib_lancado_mes", lan, periodo=p)
        add("trib_pago_mes", pg, periodo=p)
    for t, total, aberto in db.execute(select(Lancamento.tributo, func.sum(Lancamento.valor),
                                              func.sum(Lancamento.valor).filter(Lancamento.pago.is_(False)))
                                       .where(Lancamento.vencimento < hoje).group_by(G1)):
        add("trib_inadimplencia_tributo", round(100.0 * float(aberto or 0) / float(total), 1) if total else 0,
            dim=NOMES_TRIBUTO.get(t or "", t or "Outro"))
    for a, s in db.execute(select(DividaAtiva.ano, func.sum(DividaAtiva.saldo)).where(DividaAtiva.extinta.is_(False)).group_by(G1)):
        add("trib_divida_ano", s, dim=str(a or "sem ano"))
    por_devedor: dict[str, float] = defaultdict(float)  # por imóvel (codi): sem pessoa no ouro
    for codi, s in db.execute(select(DividaAtiva.codi, func.sum(DividaAtiva.saldo)).where(DividaAtiva.extinta.is_(False))
                              .group_by(G1)):
        por_devedor[codi] += float(s or 0)
    faixas = defaultdict(lambda: [0, 0.0])
    for v in por_devedor.values():
        rot = next(r for lim, r in FAIXAS if lim is None or v < lim)
        faixas[rot][0] += 1
        faixas[rot][1] += v
    for _lim, rot in FAIXAS:
        add("trib_devedores_faixa", faixas[rot][0], dim=rot)
        add("trib_saldo_faixa", faixas[rot][1], dim=rot)
    for i, v in enumerate(sorted(por_devedor.values(), reverse=True)[:20], start=1):
        add("trib_maiores_saldos", v, dim=f"{i}º")
    db.add_all(out)
    db.flush()
    return {"indicadores": len(out)}
