"""Painel Finanças (AU4/AU5, 1ª versão): agregados do AUDESP direto da prata, com filtros. Sem pessoa.

- Despesa empenhada = empenhado + anulações (que já entram negativas): é a "Despesa Total" do portal do TCE.
- Ano selecionado até o mês escolhido (padrão: o último mês publicado pelo TCE para o ano) e o mesmo período do ano
  anterior, para a comparação. Série mensal de todos os anos carregados (o filtro de mês não corta a série).
- Filtros de órgão, função e fonte valem para a despesa; a receita só tem órgão.
- Credor pessoa física só aparece somado ("pessoas físicas"); o ranking de credores é só de PJ (CNPJ), que o TCE
  já publica (transparência). Credor não é só fornecedor: o próprio município (repasses) e o INSS aparecem.
- Receita e restos a pagar não têm a função/fonte da despesa: com esses filtros, ficam de fora (None).
"""
from decimal import Decimal

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.models.financas import ArquivoAudesp, Despesa, Receita, RestoAPagar

SEM_LICITACAO = ("DISPENSA DE LICITAÇÃO", "INEXIGÍVEL")
NATUREZAS = {"pessoal": "Pessoal e encargos", "custeio": "Custeio (outras despesas correntes)", "investimento": "Investimentos",
             "juros": "Juros da dívida", "amortizacao": "Amortização da dívida", "inversao": "Inversões financeiras"}


def _f(v) -> float:
    return float(v or Decimal("0"))


def _pontos(linhas, nome=lambda d: d) -> list[dict]:
    return [{"periodo": "", "dim": nome(d) or "Não informado", "valor": _f(v)} for d, v in linhas if _f(v)]


def disponivel(db: Session) -> dict:
    anos = sorted({a for a, in db.execute(select(ArquivoAudesp.ano).where(ArquivoAudesp.tipo == "despesas"))}, reverse=True)
    ult = {a: m for a, m in db.execute(select(ArquivoAudesp.ano, ArquivoAudesp.ultimo_mes).where(ArquivoAudesp.tipo == "despesas"))}
    pub = {a: s.split("|")[0] if s else None for a, s in db.execute(
        select(ArquivoAudesp.ano, ArquivoAudesp.assinatura).where(ArquivoAudesp.tipo == "despesas"))}

    def distintos(col):
        return [x for x, in db.execute(select(col).where(col.is_not(None)).distinct().order_by(col))]
    return {"anos": anos, "ultimo_mes": {str(a): m for a, m in ult.items()}, "publicado_tce": {str(a): p for a, p in pub.items()},
            "orgaos": distintos(Despesa.orgao), "funcoes": distintos(Despesa.funcao), "fontes": distintos(Despesa.fonte_recurso),
            "carregado_em": db.scalar(select(func.max(ArquivoAudesp.carregado_em)))}


def _filtros_despesa(orgao, funcao, fonte) -> list:
    f = []
    if orgao:
        f.append(Despesa.orgao == orgao)
    if funcao:
        f.append(Despesa.funcao == funcao)
    if fonte:
        f.append(Despesa.fonte_recurso == fonte)
    return f


def _fases(db: Session, filtros: list) -> dict:
    empenhado = func.sum(case((Despesa.fase.in_(("empenhado", "anulado")), Despesa.valor), else_=0))
    liquidado = func.sum(case((Despesa.fase == "liquidado", Despesa.valor), else_=0))
    pago = func.sum(case((Despesa.fase == "pago", Despesa.valor), else_=0))
    e, liq, p = db.execute(select(empenhado, liquidado, pago).where(*filtros)).one()
    return {"empenhado": _f(e), "liquidado": _f(liq), "pago": _f(p)}


def _receita(db: Session, filtros: list) -> float:
    """Receita arrecadada total, como a "Receita Total" do portal do TCE (inclui as Intra OFSS)."""
    return _f(db.scalar(select(func.sum(Receita.valor)).where(*filtros)))


def _totais(db: Session, ano: int, mes_ate: int, orgao, funcao, fonte) -> dict | None:
    if not db.scalar(select(func.count()).select_from(Despesa).where(Despesa.ano == ano)):
        return None
    periodo = [Despesa.ano == ano, Despesa.mes <= mes_ate]
    fd = _filtros_despesa(orgao, funcao, fonte)
    t = _fases(db, periodo + fd)
    # receita e restos a pagar não têm a função/fonte da despesa: com esses filtros, não aparecem (não seria o mesmo recorte)
    so_orgao = not funcao and not fonte
    t["receita"] = _receita(db, [Receita.ano == ano, Receita.mes <= mes_ate] + ([Receita.orgao == orgao] if orgao else [])) \
        if so_orgao else None
    t["resultado"] = t["receita"] - t["liquidado"] if t["receita"] is not None else None
    emp = Despesa.fase.in_(("empenhado", "anulado"))
    sem = db.scalar(select(func.sum(Despesa.valor)).where(*periodo, *fd, emp, Despesa.modalidade_licitacao.in_(SEM_LICITACAO)))
    t["sem_licitacao_pct"] = round(100 * _f(sem) / t["empenhado"], 1) if t["empenhado"] else None
    t["pessoas_fisicas"] = _f(db.scalar(select(func.sum(Despesa.valor)).where(*periodo, *fd, emp, Despesa.credor_tipo == "PF")))
    rp = [RestoAPagar.ano_arquivo == ano, RestoAPagar.ano_balancete == ano, RestoAPagar.mes_balancete <= mes_ate]
    if orgao:
        rp.append(RestoAPagar.orgao == orgao)
    pago_rp = func.sum(RestoAPagar.pago_proc + RestoAPagar.pago_nproc - RestoAPagar.estorno_pago_proc - RestoAPagar.estorno_pago_nproc)
    t["restos_pagos"] = _f(db.scalar(select(pago_rp).where(*rp))) if so_orgao else None
    return t


def painel(db: Session, ano: int | None = None, mes_ate: int | None = None, orgao: str | None = None,
           funcao: str | None = None, fonte: str | None = None) -> dict:
    disp = disponivel(db)
    if not disp["anos"]:
        return {"disponivel": disp, "filtro": None, "atual": None, "anterior": None}
    ano = ano if ano in disp["anos"] else disp["anos"][0]
    ultimo = disp["ultimo_mes"].get(str(ano)) or 12
    mes_ate = min(max(mes_ate or ultimo, 1), 12)
    fd = _filtros_despesa(orgao, funcao, fonte)
    periodo = [Despesa.ano == ano, Despesa.mes <= mes_ate, *fd]
    emp = Despesa.fase.in_(("empenhado", "anulado"))
    liq = Despesa.fase == "liquidado"

    def mensal(fases: tuple[str, ...]) -> list[dict]:
        q = (select(Despesa.ano, Despesa.mes, func.sum(Despesa.valor)).where(*fd, Despesa.fase.in_(fases), Despesa.mes <= 12)
             .group_by(Despesa.ano, Despesa.mes))
        return [{"periodo": f"{a}-{m:02d}", "dim": "", "valor": _f(v)} for a, m, v in db.execute(q)]
    fr = [Receita.orgao == orgao] if orgao else []
    receita_mes = [{"periodo": f"{a}-{m:02d}", "dim": "", "valor": _f(v)} for a, m, v in db.execute(
        select(Receita.ano, Receita.mes, func.sum(Receita.valor)).where(*fr, Receita.mes <= 12)
        .group_by(Receita.ano, Receita.mes))]

    def soma_por(col, *extra, limite=None):
        q = select(col, func.sum(Despesa.valor)).where(*periodo, *extra).group_by(col).order_by(func.sum(Despesa.valor).desc())
        return db.execute(q.limit(limite) if limite else q).all()
    rec_filtro = [Receita.ano == ano, Receita.mes <= mes_ate, *fr]
    receita_origem = db.execute(select(Receita.subcategoria, func.sum(Receita.valor)).where(*rec_filtro)
                                .group_by(Receita.subcategoria).order_by(func.sum(Receita.valor).desc())).all()
    impostos = db.execute(select(Receita.d2, func.sum(Receita.valor)).where(*rec_filtro, Receita.subcategoria_codigo == "11000000")
                          .group_by(Receita.d2).order_by(func.sum(Receita.valor).desc())).all()
    transferencias = db.execute(select(Receita.d2, func.sum(Receita.valor)).where(*rec_filtro, Receita.subcategoria_codigo == "17000000")
                                .group_by(Receita.d2).order_by(func.sum(Receita.valor).desc())).all()
    fornecedores = db.execute(
        select(Despesa.credor_nome, func.sum(Despesa.valor)).where(*periodo, emp, Despesa.credor_tipo == "PJ")
        .group_by(Despesa.credor_nome).order_by(func.sum(Despesa.valor).desc()).limit(15)).all()
    anterior = _totais(db, ano - 1, mes_ate, orgao, funcao, fonte)
    return {
        "disponivel": disp,
        "filtro": {"ano": ano, "mes_ate": mes_ate, "orgao": orgao, "funcao": funcao, "fonte": fonte},
        "atual": _totais(db, ano, mes_ate, orgao, funcao, fonte),
        "anterior": anterior,
        "mensal": {"receita": receita_mes, "empenhado": mensal(("empenhado", "anulado")), "liquidado": mensal(("liquidado",)),
                   "pago": mensal(("pago",))},
        "por_funcao": _pontos(soma_por(Despesa.funcao, liq)),
        "por_natureza": _pontos(soma_por(Despesa.natureza, liq), lambda d: NATUREZAS.get(d, d)),
        "por_fonte": _pontos(soma_por(Despesa.fonte_recurso, liq)),
        "por_modalidade": _pontos(soma_por(Despesa.modalidade_licitacao, emp)),
        "por_orgao": _pontos(soma_por(Despesa.orgao, liq)),
        "credores_pj": _pontos(fornecedores),
        "receita_origem": _pontos(receita_origem),
        "impostos": _pontos(impostos),
        "transferencias": _pontos(transferencias),
    }


def filtro_valido(v: str | None) -> str | None:
    v = (v or "").strip()
    return v[:300] or None

