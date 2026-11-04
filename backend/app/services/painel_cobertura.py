"""Painel de cobertura de planos de saúde em Franca. Só agregados; a estimativa por bairro vem do cubo e é sempre rotulada.

Cobertura = beneficiários em plano médico-hospitalar (ANS) ÷ população estimada (IBGE). A ANS conta vínculos, não pessoas:
quem tem dois planos conta duas vezes. Por faixa, o denominador é o perfil de idade do Censo 2022 ajustado à população do ano.
"""
from sqlalchemy import text
from sqlalchemy.orm import Session

FRANCA = 351620
MIN_CIDADE = 150_000
MAX_CIDADE = 800_000


def _pop(db: Session, cd: int, ano: int) -> int | None:
    """Estimativa do ano; sem ela (ano de censo, ano novo), a mais próxima."""
    return db.scalar(text("SELECT populacao FROM ref.ibge_populacao WHERE cd_municipio = :c ORDER BY abs(ano - :a), ano DESC LIMIT 1"), {"c": cd, "a": ano})


def _pontos(linhas) -> list[dict]:
    return [{"periodo": "", "dim": str(d), "valor": float(v or 0)} for d, v in linhas]


def painel(db: Session) -> dict:
    meses = db.execute(text("SELECT ano, mes FROM silver.ans_municipio WHERE cd_municipio = :c ORDER BY ano, mes"), {"c": FRANCA}).all()
    carregado = db.scalar(text("SELECT max(carregado_em) FROM controle.ans_meses"))
    if not meses:
        return {"atual": None, "carregado_em": carregado}
    ano, mes = meses[-1]
    serie = db.execute(text("SELECT ano, mes, medica, odonto FROM silver.ans_municipio WHERE cd_municipio = :c ORDER BY ano, mes"), {"c": FRANCA}).all()
    pop = {a: _pop(db, FRANCA, a) for a in {x[0] for x in serie}}
    taxa = lambda a, n: round(100 * n / pop[a], 2) if pop.get(a) else None  # noqa: E731
    cob_mes = [{"periodo": f"{a}-{m:02d}", "dim": "", "valor": taxa(a, med) or 0} for a, m, med, _o in serie]
    ben_mes = [{"periodo": f"{a}-{m:02d}", "dim": "", "valor": med} for a, m, med, _o in serie]
    _, _, med, odo = serie[-1]
    ant = next((s for s in serie if s[0] == ano - 1 and s[1] == mes), None)
    p = {"a": ano, "m": mes}
    adc = db.execute(text("SELECT sum(aderidos), sum(cancelados) FROM silver.ans_beneficiarios WHERE ano = :a AND mes = :m AND cobertura = 'medica'"), p).one()
    # Faixa: Censo 2022 redimensionado para a população do ano
    censo = dict(db.execute(text("SELECT faixa, populacao FROM ref.ibge_faixa WHERE cd_municipio = :c"), {"c": FRANCA}).all())
    fator = (pop.get(ano) or 0) / (sum(censo.values()) or 1)
    por_faixa_n = dict(db.execute(text("SELECT faixa, sum(ativos) FROM silver.ans_beneficiarios WHERE ano = :a AND mes = :m AND cobertura = 'medica' AND faixa <> 'Inconsistente' GROUP BY 1"), p).all())
    faixas = sorted(censo, key=lambda f: int(f.split()[0]))
    taxa_faixa = [{"periodo": "", "dim": f, "valor": round(100 * float(por_faixa_n.get(f, 0)) / (censo[f] * fator), 1)} for f in faixas if censo[f] and fator]
    operadoras = _pontos(db.execute(text("SELECT operadora, sum(ativos) FROM silver.ans_beneficiarios WHERE ano = :a AND mes = :m AND cobertura = 'medica' GROUP BY 1 ORDER BY 2 DESC LIMIT 12"), p))
    contratos = _pontos(db.execute(text("SELECT contratacao, sum(ativos) FROM silver.ans_beneficiarios WHERE ano = :a AND mes = :m AND cobertura = 'medica' GROUP BY 1 ORDER BY 2 DESC"), p))
    # Cidades de SP de porte parecido, com a estimativa de população mais próxima do ano
    cidades = [{"dim": n, "cd": c, "populacao": pp, "valor": round(100 * m_ / pp, 1)} for c, n, m_, pp in db.execute(text("""
        SELECT a.cd_municipio, a.nome, a.medica, (SELECT populacao FROM ref.ibge_populacao p WHERE p.cd_municipio = a.cd_municipio ORDER BY abs(p.ano - :a), p.ano DESC LIMIT 1) AS pop
        FROM silver.ans_municipio a WHERE a.ano = :a AND a.mes = :m"""), p) if pp]
    estado_pop = sum(c["populacao"] for c in cidades)
    estado_ben = sum(c["valor"] * c["populacao"] / 100 for c in cidades)
    pares = sorted([c for c in cidades if MIN_CIDADE <= c["populacao"] <= MAX_CIDADE], key=lambda c: -c["valor"])
    posicao = next((i + 1 for i, c in enumerate(pares) if c["cd"] == FRANCA), None)
    bairros: list[dict] = []
    if db.scalar(text("SELECT to_regclass('gold.cubo_cobertura_bairro')")):
        bairros = [{"bairro": b, "pessoas": int(n), "taxa_estimada": round(100 * float(e) / float(n), 1)} for b, n, e in db.execute(text("""
            SELECT d.nome, c.pessoas, c.estimados FROM gold.cubo_cobertura_bairro c JOIN gold.dim_bairro d ON d.id = c.bairro_id
            WHERE c.bairro_id > 0 AND c.pessoas >= 100 ORDER BY 3 / NULLIF(c.pessoas, 0) DESC"""))]
    return {
        "competencia": {"ano": ano, "mes": mes}, "carregado_em": carregado,
        "atual": {"beneficiarios": med, "odonto": odo, "populacao": pop.get(ano), "taxa": taxa(ano, med),
                  "taxa_anterior": taxa(ano - 1, ant[2]) if ant else None, "beneficiarios_anterior": ant[2] if ant else None,
                  "aderidos": int(adc[0] or 0), "cancelados": int(adc[1] or 0)},
        "estado": round(100 * estado_ben / estado_pop, 1) if estado_pop else None,
        "posicao": posicao, "total_pares": len(pares),
        "cobertura_mes": cob_mes, "beneficiarios_mes": ben_mes, "taxa_faixa": taxa_faixa, "operadoras": operadoras, "contratos": contratos,
        "cidades": [{"periodo": "", "dim": c["dim"], "valor": c["valor"]} for c in pares], "bairros": bairros,
    }
