"""Consulta aos cubos (CB2): lista fechada de dimensões e medidas (nada de SQL livre), com supressão de grupos pequenos.

Respostas ficam em cache em memória até o próximo recálculo (o carimbo de gold.cubos_estado muda e a chave deixa de valer).
"""
from collections import OrderedDict

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.gold.cubos import geracao

MINIMO = 5
LIMITE_LINHAS = 5000
CACHE_MAX = 512
_cache: OrderedDict[tuple, list] = OrderedDict()

# dimensão "bairro" é o nome em gold.dim_bairro; as demais são colunas do cubo. `minimo`: a medida que define o grupo pequeno.
CUBOS: dict[str, dict] = {
    "pessoas": {"tabela": "cubo_pessoas", "dims": ("bairro", "faixa", "falecido", "consultou_12m", "vacinou_24m"), "medidas": {"pessoas": "sum(pessoas)"}, "minimo": "sum(pessoas)"},
    "saude": {"tabela": "cubo_saude", "dims": ("ano", "mes", "tipo", "bairro", "faixa", "unidade", "especialidade"),
              "medidas": {"eventos": "sum(eventos)", "espera_media": "sum(espera_soma) / NULLIF(sum(espera_n), 0)"}, "minimo": "sum(eventos)"},
    "imoveis": {"tabela": "cubo_imoveis", "dims": ("bairro", "situacao"), "medidas": {"imoveis": "sum(imoveis)", "iptu_anual": "sum(iptu_anual)", "com_possuidor": "sum(com_possuidor)"},
                "minimo": "sum(imoveis)"},
    "tributario": {"tabela": "cubo_tributario", "dims": ("exercicio", "mes", "tributo", "bairro"),
                   "medidas": {"parcelas": "sum(parcelas)", "lancado": "sum(lancado)", "pago": "sum(pago)", "vencido": "sum(vencido)", "vencido_aberto": "sum(vencido_aberto)",
                               "inadimplencia_pct": "100 * sum(vencido_aberto) / NULLIF(sum(vencido), 0)"}, "minimo": "sum(parcelas)"},
    "divida": {"tabela": "cubo_divida", "dims": ("ano", "natureza", "bairro", "faixa_saldo", "protestada", "em_processo"),
               "medidas": {"inscricoes": "sum(inscricoes)", "saldo": "sum(saldo)"}, "minimo": "sum(inscricoes)"},
    "empresas": {"tabela": "cubo_empresas", "dims": ("bairro", "tipo_pessoa", "possui_debito"), "medidas": {"empresas": "sum(empresas)"}, "minimo": "sum(empresas)"},
    "educacao": {"tabela": "cubo_educacao", "dims": ("bairro", "faixa", "ativo", "escola"), "medidas": {"alunos": "sum(alunos)"}, "minimo": "sum(alunos)"},
    "rh": {"tabela": "cubo_rh", "dims": ("ano", "mes", "secretaria", "grupo", "cargo", "faixa_salario"),
           "medidas": {"pessoas": "sum(pessoas)", "bruto": "sum(bruto)", "liquido": "sum(liquido)", "media": "sum(bruto) / NULLIF(sum(pessoas), 0)"}, "minimo": "sum(pessoas)"},
    "despesa": {"tabela": "cubo_despesa", "dims": ("ano", "mes", "orgao", "funcao", "natureza", "fase", "modalidade", "fonte"), "medidas": {"valor": "sum(valor)"}, "minimo": None},
    "receita": {"tabela": "cubo_receita", "dims": ("ano", "mes", "orgao", "subcategoria", "item"), "medidas": {"valor": "sum(valor)"}, "minimo": None},
}


class ConsultaInvalida(ValueError):
    """Cubo, dimensão, medida ou filtro fora da lista fechada. Mensagem segura para a tela."""


def catalogo() -> dict:
    return {nome: {"dimensoes": list(c["dims"]), "medidas": list(c["medidas"])} for nome, c in CUBOS.items()}


def existe(db: Session, tabela: str) -> bool:
    return db.scalar(text("SELECT to_regclass(:t)"), {"t": f"gold.{tabela}"}) is not None


def consulta(db: Session, cubo: str, agrupar: list[str], medidas: list[str] | None = None, filtros: dict[str, str] | None = None) -> list[dict]:
    c = CUBOS.get(cubo)
    if c is None:
        raise ConsultaInvalida(f"Cubo {cubo!r} não existe.")
    medidas = medidas or list(c["medidas"])
    filtros = filtros or {}
    for d in [*agrupar, *filtros]:
        if d not in c["dims"]:
            raise ConsultaInvalida(f"Dimensão {d!r} não existe no cubo {cubo}.")
    for m in medidas:
        if m not in c["medidas"]:
            raise ConsultaInvalida(f"Medida {m!r} não existe no cubo {cubo}.")
    if not existe(db, c["tabela"]):
        return []
    chave = (geracao(db), cubo, tuple(agrupar), tuple(medidas), tuple(sorted(filtros.items())))
    if chave in _cache:
        _cache.move_to_end(chave)
        return _cache[chave]
    col = lambda d: "d.nome" if d == "bairro" else f"c.{d}"  # noqa: E731
    sel = [f"{col(d)} AS {d}" for d in agrupar] + [f"{c['medidas'][m]} AS {m}" for m in medidas]
    onde, params = [], {}
    for i, (d, v) in enumerate(sorted(filtros.items())):
        onde.append(f"CAST({col(d)} AS text) = :f{i}")
        params[f"f{i}"] = v
    sql = f"SELECT {', '.join(sel)} FROM gold.{c['tabela']} c"
    if "bairro" in agrupar or "bairro" in filtros:
        sql += " JOIN gold.dim_bairro d ON d.id = c.bairro_id"
    if onde:
        sql += " WHERE " + " AND ".join(onde)
    if agrupar:
        sql += " GROUP BY " + ", ".join(col(d) for d in agrupar)
    if c["minimo"]:
        sql += f" HAVING {c['minimo']} >= {MINIMO}"
    if agrupar:
        sql += " ORDER BY " + ", ".join(col(d) for d in agrupar)
    linhas = [{k: (float(v) if hasattr(v, "as_tuple") else v) for k, v in r.items()}
              for r in db.execute(text(sql + f" LIMIT {LIMITE_LINHAS}"), params).mappings()]
    _cache[chave] = linhas
    while len(_cache) > CACHE_MAX:
        _cache.popitem(last=False)
    return linhas
