"""Cruzamentos e achados (CB3): o "mining" dos cubos. Só agregados; contagem abaixo de 5 não sai.

- Matriz por bairro: as medidas de todas as áreas lado a lado (taxas só em bairro com base suficiente, para bairro
  pequeno não virar campeão por acaso).
- Destaques: bairros muito fora da média (desvio >= 2). Correlações: pares de medidas que andam juntos entre os bairros.
  São pistas para investigar: correlação não é causa.
- Cidade: cruzamentos entre áreas (custo por atendimento, folha × receita, falecidos com imóvel, fornecedores com débito...).
  O que não tem dado carregado simplesmente não aparece.
"""
import math

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.gold.cubos import geracao

MINIMO = 5
MIN_PESSOAS = 100
MIN_IMOVEIS = 30
MIN_EMPRESAS = 20
MIN_GRUPO = 30
MIN_BAIRROS = 10
_cache: dict[str, dict] = {}

# chave -> (nome, formato, o que é "melhor": alto, baixo ou None)
MEDIDAS = {
    "pessoas": ("Pessoas no cadastro", "n", None),
    "pct_idosos": ("Idosos (60+)", "pct", None),
    "pct_criancas": ("Crianças (0 a 14)", "pct", None),
    "consultas_mil": ("Consultas por mil pessoas", "n", None),
    "pct_fora_bairro": ("Consultas em unidade de outro bairro", "pct", None),
    "pct_idosos_sem_consulta": ("Idosos sem consulta em 12 meses", "pct", "baixo"),
    "pct_criancas_sem_vacina": ("Crianças sem vacina em 24 meses", "pct", "baixo"),
    "espera_media": ("Espera por consulta (dias)", "n", "baixo"),
    "vacinas_mil": ("Vacinas por mil pessoas", "n", "alto"),
    "alunos_mil": ("Alunos da rede por mil pessoas", "n", None),
    "imoveis": ("Imóveis", "n", None),
    "iptu_medio": ("IPTU médio por imóvel", "brl", None),
    "inadimplencia": ("Inadimplência", "pct", "baixo"),
    "divida_total": ("Dívida ativa", "brl", None),
    "divida_imovel": ("Dívida ativa por imóvel", "brl", "baixo"),
    "pct_protestada": ("Dívidas protestadas", "pct", None),
    "empresas": ("Empresas", "n", None),
    "empresas_mil": ("Empresas por mil pessoas", "n", None),
    "pct_empresas_debito": ("Empresas com débito", "pct", "baixo"),
}
# Taxas: as que entram em destaques e correlações (totais só refletiriam o tamanho do bairro)
TAXAS = ("pct_idosos", "pct_criancas", "consultas_mil", "pct_fora_bairro", "pct_idosos_sem_consulta", "pct_criancas_sem_vacina", "espera_media", "vacinas_mil", "alunos_mil", "iptu_medio", "inadimplencia",
         "divida_imovel", "pct_protestada", "empresas_mil", "pct_empresas_debito")
# Pares que são a mesma coisa vista de dois lados e não contam como achado
PARES_OBVIOS = {frozenset(("pct_idosos", "pct_criancas")), frozenset(("inadimplencia", "divida_imovel")),
                frozenset(("consultas_mil", "pct_idosos_sem_consulta"))}


def _tem(db: Session, tabela: str) -> bool:
    return db.scalar(text("SELECT to_regclass(:t)"), {"t": f"gold.{tabela}"}) is not None


def _d(db: Session, sql: str) -> dict:
    return {r[0]: r[1:] for r in db.execute(text(sql))}


def _div(a, b, fator: float = 1.0) -> float | None:
    return None if not b else float(a or 0) * fator / float(b)


def matriz(db: Session) -> list[dict]:
    p = _d(db, """SELECT bairro_id, sum(pessoas) FILTER (WHERE NOT falecido),
                         sum(pessoas) FILTER (WHERE NOT falecido AND faixa = '60+'),
                         sum(pessoas) FILTER (WHERE NOT falecido AND faixa IN ('0-5', '6-14')),
                         sum(pessoas) FILTER (WHERE NOT falecido AND faixa = '60+' AND NOT consultou_12m),
                         sum(pessoas) FILTER (WHERE NOT falecido AND faixa IN ('0-5', '6-14') AND NOT vacinou_24m) FROM gold.cubo_pessoas GROUP BY 1""")
    s = _d(db, """SELECT bairro_id, sum(eventos) FILTER (WHERE tipo = 'consulta'), sum(eventos) FILTER (WHERE tipo = 'vacina'),
                         sum(espera_soma) FILTER (WHERE tipo = 'agendamento'), sum(espera_n) FILTER (WHERE tipo = 'agendamento'),
                         sum(eventos) FILTER (WHERE tipo = 'consulta' AND unidade_bairro_id > 0),
                         sum(eventos) FILTER (WHERE tipo = 'consulta' AND unidade_bairro_id > 0 AND unidade_bairro_id <> bairro_id) FROM gold.cubo_saude GROUP BY 1""")
    i = _d(db, "SELECT bairro_id, sum(imoveis), sum(iptu_anual) FROM gold.cubo_imoveis GROUP BY 1")
    t = _d(db, "SELECT bairro_id, sum(vencido), sum(vencido_aberto) FROM gold.cubo_tributario GROUP BY 1")
    d = _d(db, "SELECT bairro_id, sum(saldo), sum(inscricoes), sum(inscricoes) FILTER (WHERE protestada) FROM gold.cubo_divida GROUP BY 1")
    e = _d(db, "SELECT bairro_id, sum(empresas), sum(empresas) FILTER (WHERE possui_debito) FROM gold.cubo_empresas GROUP BY 1")
    a = _d(db, "SELECT bairro_id, sum(alunos) FROM gold.cubo_educacao WHERE ativo GROUP BY 1")
    out = []
    for bid, nome in db.execute(text("SELECT id, nome FROM gold.dim_bairro WHERE id > 0 ORDER BY nome")):
        pes, ido, cri, ido_sc, cri_sv = [float(x or 0) for x in p.get(bid, (0, 0, 0, 0, 0))]
        con, vac, esoma, en, con_loc, con_fora = [float(x or 0) for x in s.get(bid, (0, 0, 0, 0, 0, 0))]
        imo, iptu = [float(x or 0) for x in i.get(bid, (0, 0))]
        venc, abe = [float(x or 0) for x in t.get(bid, (0, 0))]
        sal, ins, pro = [float(x or 0) for x in d.get(bid, (0, 0, 0))]
        emp, deb = [float(x or 0) for x in e.get(bid, (0, 0))]
        alu = float(a.get(bid, (0,))[0] or 0)
        if not (pes >= MINIMO or imo >= MINIMO or emp >= MINIMO):
            continue  # bairro sem base nenhuma, ou variação de grafia isolada
        base_p, base_i, base_e = pes >= MIN_PESSOAS, imo >= MIN_IMOVEIS, emp >= MIN_EMPRESAS
        out.append({"bairro_id": bid, "bairro": nome, "pessoas": pes if pes >= MINIMO else None,
                    "pct_idosos": _div(ido, pes, 100) if base_p else None, "pct_criancas": _div(cri, pes, 100) if base_p else None,
                    "consultas_mil": _div(con, pes, 1000) if base_p else None, "pct_fora_bairro": _div(con_fora, con_loc, 100) if con_loc >= MIN_PESSOAS else None,
                    "pct_idosos_sem_consulta": _div(ido_sc, ido, 100) if ido >= MIN_GRUPO else None, "pct_criancas_sem_vacina": _div(cri_sv, cri, 100) if cri >= MIN_GRUPO else None,
                    "espera_media": _div(esoma, en) if en >= MIN_PESSOAS else None,
                    "vacinas_mil": _div(vac, pes, 1000) if base_p else None, "alunos_mil": _div(alu, pes, 1000) if base_p else None,
                    "imoveis": imo if imo >= MINIMO else None, "iptu_medio": _div(iptu, imo) if base_i else None,
                    "inadimplencia": _div(abe, venc, 100) if base_i and venc else None, "divida_total": sal if ins >= MINIMO else None,
                    "divida_imovel": _div(sal, imo) if base_i else None, "pct_protestada": _div(pro, ins, 100) if ins >= MIN_IMOVEIS else None,
                    "empresas": emp if emp >= MINIMO else None, "empresas_mil": _div(emp, pes, 1000) if base_p else None,
                    "pct_empresas_debito": _div(deb, emp, 100) if base_e else None})
    return out


def _fmt(v: float, formato: str) -> str:
    if formato == "pct":
        return f"{v:.1f}%".replace(".", ",")
    if formato == "brl":
        return "R$ " + f"{v:,.0f}".replace(",", ".")
    return f"{v:,.0f}".replace(",", ".") if abs(v) >= 100 or float(v).is_integer() else f"{v:.1f}".replace(".", ",")


def destaques(linhas: list[dict], por_medida: int = 2, total: int = 14) -> list[dict]:
    """Bairros a pelo menos 2 desvios da média, nos dois lados; os mais extremos primeiro."""
    achados = []
    for m in TAXAS:
        pares = [(x["bairro"], x[m]) for x in linhas if x.get(m) is not None]
        if len(pares) < MIN_BAIRROS:
            continue
        vs = [v for _, v in pares]
        media = sum(vs) / len(vs)
        dp = math.sqrt(sum((v - media) ** 2 for v in vs) / len(vs))
        if dp == 0:
            continue
        nome, fmt, bom = MEDIDAS[m]
        fora = sorted(((b, v, (v - media) / dp) for b, v in pares if abs(v - media) >= 2 * dp), key=lambda x: -abs(x[2]))
        for b, v, z in fora[:por_medida]:
            sinal = None if bom is None else ("bom" if (z > 0) == (bom == "alto") else "ruim")
            vezes = f", {v / media:.1f}× a média".replace(".", ",") if media > 0 and z > 0 else f" (média {_fmt(media, fmt)})"
            achados.append({"bairro": b, "medida": m, "valor": v, "media": media, "z": round(z, 2), "sinal": sinal,
                            "frase": f"{nome}: {_fmt(v, fmt)}{vezes}."})
    return sorted(achados, key=lambda a: -abs(a["z"]))[:total]


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    sy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if sx == 0 or sy == 0:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True)) / (sx * sy)


def correlacoes(linhas: list[dict], minimo_r: float = 0.4, total: int = 12) -> list[dict]:
    out = []
    for i, a in enumerate(TAXAS):
        for b in TAXAS[i + 1:]:
            if frozenset((a, b)) in PARES_OBVIOS:
                continue
            pares = [(x[a], x[b]) for x in linhas if x.get(a) is not None and x.get(b) is not None]
            if len(pares) < MIN_BAIRROS:
                continue
            r = _pearson([p[0] for p in pares], [p[1] for p in pares])
            if r is None or abs(r) < minimo_r:
                continue
            na, nb = MEDIDAS[a][0], MEDIDAS[b][0]
            out.append({"a": a, "b": b, "r": round(r, 2), "bairros": len(pares),
                        "frase": f"Onde {na.lower()} é maior, {nb.lower()} tende a ser {'maior' if r > 0 else 'menor'}."})
    return sorted(out, key=lambda c: -abs(c["r"]))[:total]


def _achado(cid: str, titulo: str, valor, formato: str, detalhe: str, areas: list[str], acao: str | None = None) -> dict:
    return {"id": cid, "titulo": titulo, "valor": float(valor), "formato": formato, "detalhe": detalhe, "areas": areas, "acao": acao}


def _periodo_comum(db: Session) -> tuple[int, int] | None:
    """Último ano do AUDESP e até que mês ele vai: as comparações entre áreas usam só os mesmos meses."""
    r = db.execute(text("SELECT ano, max(mes) FROM gold.cubo_despesa WHERE mes <= 12 GROUP BY ano ORDER BY ano DESC LIMIT 1")).first()
    return (r[0], r[1]) if r else None


def cidade(db: Session) -> list[dict]:
    out: list[dict] = []
    per = _periodo_comum(db)
    if per:
        ano, mes = per
        rot = f"jan a {mes:02d}/{ano}"
        # o nome da função vem como o AUDESP publica; compara sem acento e sem depender de maiúsculas
        liq = lambda f: db.scalar(text("SELECT sum(valor) FROM gold.cubo_despesa WHERE ano = :a AND mes <= :m AND fase = 'liquidado' AND gold.norm_bairro(funcao) LIKE :f"), {"a": ano, "m": mes, "f": f"%{f}"})  # noqa: E731
        saude, educ = liq("SAUDE"), liq("EDUCACAO")
        cons = db.scalar(text("SELECT sum(eventos) FROM gold.cubo_saude WHERE tipo = 'consulta' AND ano = :a AND mes <= :m"), {"a": ano, "m": mes})
        if saude and cons:
            out.append(_achado("custo_consulta", "Gasto da Saúde por consulta", float(saude) / float(cons), "brl",
                               f"Despesa liquidada na função Saúde ÷ {_fmt(float(cons), 'n')} consultas ({rot}).", ["AUDESP", "Saúde"]))
        alu = db.scalar(text("SELECT sum(alunos) FROM gold.cubo_educacao WHERE ativo"))
        if educ and alu:
            out.append(_achado("custo_aluno", "Gasto da Educação por aluno", float(educ) / float(alu), "brl",
                               f"Despesa liquidada na função Educação ÷ {_fmt(float(alu), 'n')} alunos ativos ({rot}).", ["AUDESP", "Educação"]))
        rec = db.scalar(text("SELECT sum(valor) FROM gold.cubo_receita WHERE ano = :a AND mes <= :m"), {"a": ano, "m": mes})
        fol = db.scalar(text("SELECT sum(bruto) FROM gold.cubo_rh WHERE ano = :a AND mes <= :m"), {"a": ano, "m": mes})
        if rec and fol:
            out.append(_achado("folha_receita", "Folha bruta sobre a receita", 100 * float(fol) / float(rec), "pct",
                               f"Folha do Portal da Transparência ÷ receita do AUDESP ({rot}). Não é o limite oficial da LRF.", ["RH", "AUDESP"]))
        pes = db.scalar(text("SELECT sum(valor) FROM gold.cubo_despesa WHERE ano = :a AND mes <= :m AND fase = 'liquidado' AND natureza = 'pessoal'"), {"a": ano, "m": mes})
        if pes and fol:
            out.append(_achado("folha_audesp", "Folha do portal sobre o pessoal do AUDESP", 100 * float(fol) / float(pes), "pct",
                               f"Conferência entre fontes ({rot}): a diferença inclui encargos, indiretas e o que cada fonte conta.", ["RH", "AUDESP"]))
        rec0 = db.scalar(text("SELECT sum(valor) FROM gold.cubo_receita WHERE ano = :a AND mes <= :m"), {"a": ano - 1, "m": mes})
        fol0 = db.scalar(text("SELECT sum(bruto) FROM gold.cubo_rh WHERE ano = :a AND mes <= :m"), {"a": ano - 1, "m": mes})
        if rec and fol and rec0 and fol0:
            df, dr = 100 * (float(fol) / float(fol0) - 1), 100 * (float(rec) / float(rec0) - 1)
            out.append(_achado("folha_x_receita", "Folha cresceu acima da receita?", df - dr, "pp",
                               f"Em relação a {ano - 1}, folha {_fmt(df, 'pct')} e receita {_fmt(dr, 'pct')} ({rot}).", ["RH", "AUDESP"]))
        fornecedores = db.execute(text("""
            WITH c AS (SELECT credor_cnpj, sum(valor) AS v FROM silver.despesa WHERE ano = :a AND fase IN ('empenhado', 'anulado') AND credor_cnpj IS NOT NULL GROUP BY 1 HAVING sum(valor) > 0)
            SELECT count(*), COALESCE(sum(c.v), 0) FROM c JOIN (SELECT DISTINCT documento FROM silver.empresas_sil WHERE possui_debito) e ON e.documento = c.credor_cnpj
        """), {"a": ano}).one()
        if fornecedores[0] >= MINIMO:
            out.append(_achado("fornecedor_debito", "Fornecedores com débito no município", fornecedores[0], "n",
                               f"Credores de {ano} com débito na SIL; receberam {_fmt(float(fornecedores[1]), 'brl')} em empenhos.", ["AUDESP", "SIL"],
                               "Confirmar a regularidade fiscal antes de novos pagamentos."))
        divida_forn = db.execute(text("""
            WITH c AS (SELECT DISTINCT credor_cnpj FROM silver.despesa WHERE ano = :a AND credor_cnpj IS NOT NULL)
            SELECT count(DISTINCT p.documento), COALESCE(sum(d.saldo), 0) FROM c
            JOIN silver.imovel_possuidores p ON p.documento = c.credor_cnpj AND p.ativo
            JOIN silver.dividas d ON d.codi = p.codi AND d.extinta IS NOT TRUE AND d.saldo > 0
        """), {"a": ano}).one()
        if divida_forn[0] >= MINIMO:
            out.append(_achado("fornecedor_divida", "Fornecedores com imóvel em dívida ativa", divida_forn[0], "n",
                               f"Credores de {ano} donos de imóvel com {_fmt(float(divida_forn[1]), 'brl')} em dívida ativa.", ["AUDESP", "IPTU", "Dívida"],
                               "Avaliar compensação ou cobrança."))
    fal = db.execute(text("""SELECT count(DISTINCT c.cidadao_id), count(DISTINCT p.codi) FROM silver.cidadaos c
                             JOIN silver.imovel_possuidores p ON p.cidadao_id = c.cidadao_id AND p.ativo WHERE c.falecido""")).one()
    if fal[0] >= MINIMO:
        out.append(_achado("falecido_imovel", "Falecidos ainda donos de imóvel", fal[0], "n", f"Pessoas com óbito registrado e posse ativa de {_fmt(float(fal[1]), 'n')} imóveis.",
                           ["Cemitério/Saúde", "IPTU"], "Atualizar o cadastro: carnês podem estar indo para quem morreu."))
    fal_d = db.execute(text("""SELECT count(DISTINCT c.cidadao_id), COALESCE(sum(l.valor), 0) FROM silver.cidadaos c
                               JOIN silver.lancamentos l ON l.cidadao_id = c.cidadao_id AND l.pago IS NOT TRUE AND l.vencimento < current_date WHERE c.falecido""")).one()
    if fal_d[0] >= MINIMO:
        out.append(_achado("falecido_debito", "Débito vencido em nome de falecidos", fal_d[1], "brl", f"{_fmt(float(fal_d[0]), 'n')} pessoas com óbito registrado e parcelas vencidas em aberto.",
                           ["Cemitério/Saúde", "Fazenda"], "Separar da cobrança comum: o caminho é o espólio."))
    vac = db.execute(text("""SELECT count(*), count(*) FILTER (WHERE NOT p.vacinou_24m) FROM silver.escola_alunos a JOIN gold.cubo_aux_pessoa p ON p.cidadao_id = a.cidadao_id
                             WHERE a.excluido IS NOT TRUE AND a.situacao = 1 AND a.data_nascimento > current_date - interval '15 years'""")).one()
    if vac[1] >= MINIMO:
        out.append(_achado("aluno_sem_vacina", "Alunos sem vacina registrada", vac[1], "n",
                           f"{_fmt(100 * vac[1] / vac[0], 'pct')} dos {_fmt(float(vac[0]), 'n')} alunos até 14 anos ligados ao cadastro não têm vacina nos últimos 24 meses na rede.",
                           ["Educação", "Saúde"], "Busca ativa com as escolas. Pode ser vacina dada fora da rede municipal."))
    ido = db.execute(text("SELECT count(*), count(*) FILTER (WHERE NOT consultou_12m) FROM gold.cubo_aux_pessoa WHERE faixa = '60+' AND NOT falecido")).one()
    if ido[1] >= MINIMO:
        out.append(_achado("idoso_sem_consulta", "Idosos sem consulta em 12 meses", ido[1], "n",
                           f"{_fmt(100 * ido[1] / ido[0], 'pct')} dos {_fmt(float(ido[0]), 'n')} idosos do cadastro não têm consulta na rede nos últimos 12 meses (com os dados carregados).",
                           ["Cadastro", "Saúde"], "Cruzar com o bairro para a atenção básica."))
    conc = db.execute(text("""WITH v AS (SELECT codi, sum(saldo) AS s FROM silver.dividas WHERE extinta IS NOT TRUE AND saldo > 0 GROUP BY 1),
                              r AS (SELECT s, ntile(100) OVER (ORDER BY s DESC) AS q FROM v)
                              SELECT count(*), sum(s) FILTER (WHERE q = 1), sum(s) FROM r""")).one()
    if conc[0] and conc[0] >= 100:
        out.append(_achado("concentracao_divida", "Dívida ativa no 1% de imóveis mais devedores", 100 * float(conc[1]) / float(conc[2]), "pct",
                           f"De {_fmt(float(conc[2]), 'brl')} em {_fmt(float(conc[0]), 'n')} imóveis com dívida.", ["Dívida"], "Cobrança priorizada onde o retorno é maior."))
    sem = db.scalar(text("""SELECT count(*) FROM silver.imoveis i WHERE NOT EXISTS (SELECT 1 FROM silver.imovel_possuidores p WHERE p.codi = i.codi AND p.ativo)"""))
    if sem and sem >= MINIMO:
        out.append(_achado("imovel_sem_dono", "Imóveis sem possuidor ativo", sem, "n", "Cadastro de IPTU sem nenhum dono vigente: ninguém a quem cobrar.", ["IPTU"], "Revisão cadastral."))
    multi = db.execute(text("""WITH m AS (SELECT documento, count(DISTINCT codi) AS n FROM silver.imovel_possuidores WHERE ativo AND tipo_pessoa = 'PF' GROUP BY 1)
                               SELECT count(*) FILTER (WHERE n >= 5), COALESCE(sum(n) FILTER (WHERE n >= 5), 0), count(*) FROM m""")).one()
    if multi[0] >= MINIMO:
        out.append(_achado("multi_imoveis", "Pessoas com 5 imóveis ou mais", multi[0], "n", f"Juntas têm {_fmt(float(multi[1]), 'n')} imóveis, de {_fmt(float(multi[2]), 'n')} pessoas físicas donas de imóvel.",
                           ["IPTU"]))
    return out


def tudo(db: Session) -> dict:
    """Resposta da tela, em cache em memória até o próximo recálculo dos cubos."""
    faltam = [t for t in ("dim_bairro", "cubo_pessoas", "cubo_saude", "cubo_imoveis", "cubo_tributario", "cubo_divida", "cubo_empresas",
                          "cubo_educacao", "cubo_rh", "cubo_despesa", "cubo_receita", "cubo_aux_pessoa") if not _tem(db, t)]
    if faltam:
        return {"calculado": False, "medidas": [], "bairros": [], "destaques": [], "correlacoes": [], "cidade": []}
    gen = geracao(db) or ""
    if gen in _cache:
        return _cache[gen]
    linhas = matriz(db)
    r = {"calculado": True, "gerado_em": gen, "medidas": [{"chave": k, "nome": v[0], "formato": v[1], "bom": v[2]} for k, v in MEDIDAS.items()],
         "bairros": linhas, "destaques": destaques(linhas), "correlacoes": correlacoes(linhas), "cidade": cidade(db)}
    _cache.clear()
    _cache[gen] = r
    return r
