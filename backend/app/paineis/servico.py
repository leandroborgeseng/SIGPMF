"""Shell de painéis (onda 0, P01–P08, S01–S02, K01–K04). Lê SÓ o ouro (gold.painel_fato) e controle.execucoes.

Nada de prata/bronze em tempo de requisição (S02) e nenhuma pessoa na resposta (K03): o fato já é agregado por
competência × secretaria × órgão × rubrica, com `n` = pessoas no grupo, que decide a supressão (privacidade.py).

KPI = função de m1 (numerador aditivo), m2 (segunda medida aditiva) e n. Cada painel declara os seus no registro PAINEIS.
"""
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.paineis.privacidade import k_do_cubo, suprime, valor_protegido

MESES = ("jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez")
SERIE_MESES = 12
NIVEIS_COL = ("nivel1", "nivel2", "nivel3")


@dataclass(frozen=True)
class Kpi:
    chave: str
    rotulo: str
    formato: str  # brl | pct | n
    calculo: str  # m1 | m2 | n | razao_m2_m1 (m2/m1*100) | media_m1_n (m1/n)
    alta_e_boa: bool
    ajuda: str


@dataclass(frozen=True)
class Painel:
    chave: str
    titulo: str
    cubo: str
    sistema: str  # Execucao.sistema que alimenta o painel (selo de fonte/data, P03)
    niveis: tuple[str, ...]
    kpis: tuple[Kpi, ...]
    medida_drill: str
    exemplo: bool = False
    fonte_rotulo: str = ""
    extras: dict = field(default_factory=dict)


FOLHA_MODELO = Painel(
    chave="folha-modelo", titulo="Folha de pagamento (modelo)", cubo="folha_modelo", sistema="EXEMPLO_FOLHA",
    niveis=("Secretaria", "Órgão/lotação", "Rubrica"), medida_drill="pct_he", exemplo=True,
    fonte_rotulo="Dados sintéticos de exemplo (sem carga real da folha)",
    kpis=(
        Kpi("folha_bruta", "Folha bruta do mês", "brl", "m1", False, "Soma dos vencimentos brutos da competência."),
        Kpi("horas_extras", "Horas extras (R$)", "brl", "m2", False, "Soma das rubricas de horas extras da competência."),
        Kpi("pct_he", "% horas extras sobre a folha", "pct", "razao_m2_m1", False, "Horas extras ÷ folha bruta × 100."),
        Kpi("servidores", "Servidores pagos", "n", "n", True, "Servidores com pagamento na competência."),
        Kpi("custo_medio", "Custo médio por servidor", "brl", "media_m1_n", False, "Folha bruta ÷ servidores pagos."),
    ),
)
PAINEIS: dict[str, Painel] = {FOLHA_MODELO.chave: FOLHA_MODELO}

STATUS_OK = ("sucesso", "parcial", "concluida")


class AcessoNegado(Exception):
    pass


class PedidoInvalido(ValueError):
    pass


def calcula(k: Kpi, m1: float, m2: float, n: int) -> float | None:
    if k.calculo == "m1":
        return m1
    if k.calculo == "m2":
        return m2
    if k.calculo == "n":
        return float(n)
    if k.calculo == "razao_m2_m1":
        return round(100.0 * m2 / m1, 2) if m1 else None
    if k.calculo == "media_m1_n":
        return round(m1 / n, 2) if n else None
    raise ValueError(k.calculo)


def rotulo_mes(comp: str) -> str:
    a, m = comp.split("-")
    return f"{MESES[int(m) - 1]}/{a}"


def desloca(comp: str, meses: int) -> str:
    a, m = (int(x) for x in comp.split("-"))
    t = a * 12 + (m - 1) + meses
    return f"{t // 12:04d}-{t % 12 + 1:02d}"


def _valida_comp(comp: str | None) -> str | None:
    if comp is None or comp == "":
        return None
    if len(comp) != 7 or comp[4] != "-" or not (comp[:4] + comp[5:]).isdigit() or not 1 <= int(comp[5:]) <= 12:
        raise PedidoInvalido("Competência inválida (use AAAA-MM).")
    return comp


def fonte(db: Session, p: Painel) -> dict | None:
    """Última execução bem-sucedida do sistema do painel (P03): nunca uma data fixa no código."""
    r = db.execute(
        text("SELECT id, sistema, status, COALESCE(finalizado_em, iniciado_em) AS em FROM controle.execucoes "
             "WHERE sistema = :s AND status = ANY(:ok) ORDER BY COALESCE(finalizado_em, iniciado_em) DESC, id DESC LIMIT 1"),
        {"s": p.sistema, "ok": list(STATUS_OK)}).mappings().first()
    if not r:
        return None
    em: datetime = r["em"]
    return {"sistema": r["sistema"], "rotulo": p.fonte_rotulo or r["sistema"], "execucao_id": r["id"],
            "carga_em": em.isoformat() if em else None}


def _filtros(p: Painel, escopo: frozenset[str] | None, secretaria: str | None, caminho: list[str]) -> tuple[str, dict]:
    onde, prm = ["painel = :painel"], {"painel": p.chave}
    if escopo is not None:
        onde.append("nivel1 = ANY(:escopo)")
        prm["escopo"] = sorted(escopo)
    if secretaria:
        onde.append("nivel1 = :sec")
        prm["sec"] = secretaria
    for i, v in enumerate(caminho):
        col = NIVEIS_COL[i + (1 if secretaria else 0)]
        onde.append(f"{col} = :c{i}")
        prm[f"c{i}"] = v
    return " AND ".join(onde), prm


def painel(db: Session, chave: str, *, competencia: str | None, secretaria: str | None, caminho: list[str],
           escopo: frozenset[str] | None) -> dict:
    """`escopo`: None = vê tudo (prefeito); conjunto = só essas secretarias (checado aqui, no backend, P08)."""
    p = PAINEIS.get(chave)
    if p is None:
        raise PedidoInvalido("Painel não existe.")
    competencia = _valida_comp(competencia)
    secretaria = (secretaria or "").strip()[:80] or None
    caminho = [c.strip()[:120] for c in caminho if c.strip()]
    if caminho and not secretaria and not (escopo is not None and len(escopo) == 1):
        raise PedidoInvalido("Escolha a secretaria antes de detalhar.")
    if escopo is not None and secretaria and secretaria not in escopo:
        raise AcessoNegado()
    if escopo is not None and not secretaria and len(escopo) == 1:
        secretaria = next(iter(escopo))  # secretário de uma secretaria: o filtro já vem fixo
    if len(caminho) + (1 if secretaria else 0) >= len(p.niveis):
        raise PedidoInvalido("Nível de detalhe inválido.")
    k = k_do_cubo(p.cubo)
    base = {"painel": p.chave, "titulo": p.titulo, "exemplo": p.exemplo, "k": k, "niveis": list(p.niveis),
            "filtros": {"competencia": competencia, "secretaria": secretaria, "caminho": caminho},
            "secretarias": [], "competencias": [], "kpis": [], "drill": None}
    base["fonte"] = fonte(db, p)
    if base["fonte"] is None:
        return {**base, "estado": "sem_fonte"}
    onde, prm = _filtros(p, escopo, None, [])
    base["secretarias"] = [s for s, in db.execute(text(f"SELECT DISTINCT nivel1 FROM gold.painel_fato WHERE {onde} ORDER BY 1"), prm)]
    comps = [c for c, in db.execute(text(f"SELECT DISTINCT competencia FROM gold.painel_fato WHERE {onde} ORDER BY 1"), prm)]
    base["competencias"] = [{"valor": c, "rotulo": rotulo_mes(c)} for c in comps]
    if not comps:
        return {**base, "estado": "vazio"}
    if competencia is None:
        competencia = comps[-1]
        base["filtros"]["competencia"] = competencia
    onde, prm = _filtros(p, escopo, secretaria, caminho)
    ini = desloca(competencia, -(SERIE_MESES + 1))  # 12 meses de série + o mesmo mês do ano anterior
    por_mes = {c: (float(m1), float(m2), int(n)) for c, m1, m2, n in db.execute(text(
        f"SELECT competencia, sum(m1), sum(m2), sum(n) FROM gold.painel_fato WHERE {onde} AND competencia BETWEEN :ini AND :fim "
        "GROUP BY 1"), {**prm, "ini": ini, "fim": competencia})}
    if competencia not in por_mes:
        return {**base, "estado": "vazio", "ultima_competencia": {"valor": comps[-1], "rotulo": rotulo_mes(comps[-1])}}
    meses = [desloca(competencia, -i) for i in range(SERIE_MESES - 1, -1, -1)]

    def val(kp: Kpi, comp: str) -> float | None:
        if comp not in por_mes:
            return None  # lacuna: mês sem carga nunca vira 0 (P02)
        m1, m2, n = por_mes[comp]
        if valor_protegido(n, k):
            return None
        return calcula(kp, m1, m2, n)

    n_atual = por_mes[competencia][2]
    protegido = valor_protegido(n_atual, k)
    for kp in p.kpis:
        atual = None if protegido else val(kp, competencia)
        base["kpis"].append({
            "chave": kp.chave, "rotulo": kp.rotulo, "formato": kp.formato, "calculo": kp.calculo, "alta_e_boa": kp.alta_e_boa, "ajuda": kp.ajuda,
            "valor": atual, "suprimido": protegido,
            "mom": _variacao(kp, atual, val(kp, desloca(competencia, -1)), desloca(competencia, -1)),
            "yoy": _variacao(kp, atual, val(kp, desloca(competencia, -12)), desloca(competencia, -12)),
            "serie": [{"competencia": c, "rotulo": rotulo_mes(c), "valor": val(kp, c), "sem_carga": c not in por_mes} for c in meses],
        })
    base["drill"] = _drill(db, p, onde, prm, competencia, secretaria, caminho, k, nomes_secretarias(db))
    return {**base, "estado": "ok"}


def _variacao(kp: Kpi, atual: float | None, anterior: float | None, comp: str) -> dict:
    """Δ com sinal; `None` (tela mostra "—") quando depende de mês sem carga ou suprimido (P02)."""
    out = {"referencia": rotulo_mes(comp), "delta": None, "pct": None, "sentido": None}
    if atual is None or anterior is None:
        return out
    delta = round(atual - anterior, 2)
    out["delta"] = delta
    out["pct"] = round(100.0 * delta / anterior, 1) if anterior and kp.formato != "pct" else None
    out["sentido"] = "alta" if delta > 0 else "queda" if delta < 0 else "estavel"
    return out


def nomes_secretarias(db: Session) -> dict[str, str]:
    return {c: n for c, n in db.execute(text("SELECT codigo, nome FROM controle.secretarias"))}


def _drill(db: Session, p: Painel, onde: str, prm: dict, comp: str, secretaria: str | None, caminho: list[str], k: int,
           nomes: dict[str, str]) -> dict:
    nivel = len(caminho) + (1 if secretaria else 0)
    col = NIVEIS_COL[nivel]
    kp = next(x for x in p.kpis if x.chave == p.medida_drill)
    linhas = [{"rotulo": r or "Não informado", "m1": float(m1), "m2": float(m2), "n": int(n)} for r, m1, m2, n in db.execute(text(
        f"SELECT {col}, sum(m1), sum(m2), sum(n) FROM gold.painel_fato WHERE {onde} AND competencia = :comp GROUP BY 1 ORDER BY 1"),
        {**prm, "comp": comp})]
    tot = {"m1": sum(x["m1"] for x in linhas), "m2": sum(x["m2"] for x in linhas), "n": sum(x["n"] for x in linhas)}
    itens, ocultos = suprime(linhas, k)
    pode_abrir = nivel + 1 < len(p.niveis)
    out_itens = []
    for i in sorted(itens, key=lambda x: (bool(x.get("outros")), -(calcula(kp, x["m1"], x["m2"], x["n"]) or 0))):
        out_itens.append({"rotulo": nomes.get(i["rotulo"], i["rotulo"]) if nivel == 0 else i["rotulo"], "chave": i["rotulo"], "m1": i["m1"], "m2": i["m2"], "n": i["n"], "valor": calcula(kp, i["m1"], i["m2"], i["n"]),
                          "outros": bool(i.get("outros")), "abre": pode_abrir and not i.get("outros")})
    migalhas = [{"rotulo": "Prefeitura", "secretaria": None, "caminho": []}]
    if secretaria:
        migalhas.append({"rotulo": nomes.get(secretaria, secretaria), "secretaria": secretaria, "caminho": []})
    for i, c in enumerate(caminho):
        migalhas.append({"rotulo": c, "secretaria": secretaria, "caminho": caminho[: i + 1]})
    total_protegido = valor_protegido(tot["n"], k)
    return {"nivel": p.niveis[nivel], "indice": nivel, "medida": {"chave": kp.chave, "rotulo": kp.rotulo, "formato": kp.formato},
            "migalhas": migalhas, "itens": [] if total_protegido else out_itens, "ocultos": ocultos, "suprimido": total_protegido,
            "total": None if total_protegido else {**tot, "valor": calcula(kp, tot["m1"], tot["m2"], tot["n"])}}
