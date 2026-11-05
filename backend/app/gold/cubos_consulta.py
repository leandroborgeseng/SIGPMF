"""Consulta aos cubos (CB2): lista fechada de dimensões e medidas (nada de SQL livre), com supressão de grupos pequenos.

Respostas ficam em cache em memória até o próximo recálculo (o carimbo de gold.cubos_estado muda e a chave deixa de valer).
"""
from collections import OrderedDict

from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import Session

from app.gold.cubos import geracao
from app.paineis.privacidade import k_do_cubo

MINIMO = 5  # padrão; o corte de cada cubo vem de app.paineis.privacidade.k_do_cubo (ponto único K02)
LIMITE_LINHAS = 5000
CACHE_MAX = 512
_cache: OrderedDict[tuple, dict] = OrderedDict()

# Nome de tela e, quando há, ordem própria (faixas) e rótulo dos valores gravados no cubo
DIMENSOES: dict[str, dict] = {
    "ano": {"nome": "Ano"}, "ano_venc": {"nome": "Ano de vencimento"}, "exercicio": {"nome": "Exercício"}, "mes": {"nome": "Mês"},
    "bairro": {"nome": "Bairro"}, "unidade": {"nome": "Unidade"}, "especialidade": {"nome": "Especialidade"}, "vacina": {"nome": "Vacina"},
    "tipo": {"nome": "Tipo", "rotulos": {"consulta": "Consulta", "agendamento": "Agendamento", "vacina": "Vacina"}},
    "faixa": {"nome": "Faixa etária", "ordem": ["0-5", "6-14", "15-17", "18-29", "30-59", "60+", "não informada"]},
    "falecido": {"nome": "Falecido"}, "consultou_12m": {"nome": "Consultou em 12 meses"}, "vacinou_24m": {"nome": "Vacinou em 24 meses"},
    "situacao": {"nome": "Situação"}, "tributo": {"nome": "Tributo"},
    "natureza": {"nome": "Natureza", "rotulos": {"pessoal": "Pessoal e encargos", "custeio": "Custeio", "investimento": "Investimentos", "outra": "Outras"}},
    "faixa_saldo": {"nome": "Faixa de saldo", "ordem": ["até R$ 1 mil", "R$ 1 a 10 mil", "R$ 10 a 100 mil", "acima de R$ 100 mil"]},
    "protestada": {"nome": "Protestada"}, "em_processo": {"nome": "Em processo"}, "tipo_pessoa": {"nome": "Tipo de pessoa"}, "possui_debito": {"nome": "Com débito"},
    "ativo": {"nome": "Aluno ativo"}, "escola": {"nome": "Escola"}, "secretaria": {"nome": "Secretaria"}, "cargo": {"nome": "Cargo"},
    "grupo": {"nome": "Vínculo", "rotulos": {"ativa": "Em atividade", "inativos": "Aposentados e pensionistas", "outros": "Cedidos e indenizados"}},
    "faixa_salario": {"nome": "Faixa de remuneração", "ordem": ["até R$ 3 mil", "R$ 3 a 5 mil", "R$ 5 a 8 mil", "R$ 8 a 12 mil", "R$ 12 a 20 mil", "acima de R$ 20 mil"]},
    "orgao": {"nome": "Órgão"}, "funcao": {"nome": "Função"}, "fase": {"nome": "Fase"}, "modalidade": {"nome": "Modalidade de licitação"}, "fonte": {"nome": "Fonte de recurso"},
    "subcategoria": {"nome": "Subcategoria"}, "item": {"nome": "Item"},
}
# chave -> (nome, formato, se somar faz sentido)
MEDIDAS: dict[str, tuple[str, str, bool]] = {
    "pessoas": ("Pessoas", "n", True), "eventos": ("Eventos", "n", True), "espera_media": ("Espera média", "dias", False),
    "imoveis": ("Imóveis", "n", True), "iptu_anual": ("IPTU anual", "brl", True), "com_possuidor": ("Com possuidor", "n", True),
    "parcelas": ("Parcelas", "n", True), "lancado": ("Lançado", "brl", True), "pago": ("Pago", "brl", True), "vencido": ("Vencido", "brl", True),
    "vencido_aberto": ("Vencido em aberto", "brl", True), "inadimplencia_pct": ("Inadimplência", "pct", False),
    "inscricoes": ("Inscrições", "n", True), "saldo": ("Saldo", "brl", True), "empresas": ("Empresas", "n", True), "alunos": ("Alunos", "n", True),
    "bruto": ("Folha bruta", "brl", True), "liquido": ("Folha líquida", "brl", True), "media": ("Remuneração média", "brl", False), "valor": ("Valor", "brl", True),
}
# dimensão "bairro" é o nome em gold.dim_bairro; as demais são colunas do cubo. `minimo`: a medida que define o grupo pequeno.
CUBOS: dict[str, dict] = {
    "pessoas": {"tabela": "cubo_pessoas", "dims": ("bairro", "faixa", "falecido", "consultou_12m", "vacinou_24m"), "medidas": {"pessoas": "sum(pessoas)"}, "minimo": "sum(pessoas)"},
    "saude": {"tabela": "cubo_saude", "dims": ("ano", "mes", "tipo", "bairro", "faixa", "unidade", "especialidade", "vacina"),
              "medidas": {"eventos": "sum(eventos)", "espera_media": "sum(espera_soma) / NULLIF(sum(espera_n), 0)"}, "minimo": "sum(eventos)"},
    "imoveis": {"tabela": "cubo_imoveis", "dims": ("bairro", "situacao"), "medidas": {"imoveis": "sum(imoveis)", "iptu_anual": "sum(iptu_anual)", "com_possuidor": "sum(com_possuidor)"},
                "minimo": "sum(imoveis)"},
    "tributario": {"tabela": "cubo_tributario", "dims": ("exercicio", "ano_venc", "mes", "tributo", "bairro"),
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
TEMPO = {"ano", "ano_venc", "exercicio", "mes"}


class ConsultaInvalida(ValueError):
    """Cubo, dimensão, medida ou filtro fora da lista fechada. Mensagem segura para a tela."""


def catalogo() -> dict:
    return {nome: {"dimensoes": [{"chave": d, **DIMENSOES[d]} for d in c["dims"]],
                   "medidas": [{"chave": m, "nome": MEDIDAS[m][0], "formato": MEDIDAS[m][1], "aditiva": MEDIDAS[m][2]} for m in c["medidas"]]}
            for nome, c in CUBOS.items()}


def existe(db: Session, tabela: str) -> bool:
    return db.scalar(text("SELECT to_regclass(:t)"), {"t": f"gold.{tabela}"}) is not None


def consulta(db: Session, cubo: str, agrupar: list[str], medidas: list[str] | None = None, filtros: dict[str, str] | None = None,
             mes_ate: int | None = None) -> dict:
    """`linhas` agregadas e quantos grupos ficaram de fora por serem pequenos (`ocultos`). `calculado` é falso até o primeiro
    recálculo e também enquanto o cubo gravado for de uma versão sem a coluna pedida (o próximo recálculo resolve)."""
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
    if mes_ate is not None and "mes" not in c["dims"]:
        raise ConsultaInvalida(f"O cubo {cubo} não tem mês.")
    vazio = {"linhas": [], "ocultos": 0, "truncado": False, "calculado": False}
    if not existe(db, c["tabela"]):
        return vazio
    chave = (geracao(db), cubo, tuple(agrupar), tuple(medidas), tuple(sorted(filtros.items())), mes_ate)
    if chave in _cache:
        _cache.move_to_end(chave)
        return _cache[chave]
    col = lambda d: "d.nome" if d == "bairro" else f"c.{d}"  # noqa: E731
    sel = [f"{col(d)} AS {d}" for d in agrupar] + [f"{c['medidas'][m]} AS {m}" for m in medidas]
    onde, params = [], {}
    for i, (d, v) in enumerate(sorted(filtros.items())):
        onde.append(f"CAST({col(d)} AS text) = :f{i}")
        params[f"f{i}"] = v
    if mes_ate is not None:
        onde.append("c.mes <= :mes_ate")
        params["mes_ate"] = mes_ate
    sql = f"SELECT {', '.join(sel)}, {c['minimo'] or 'count(*)'} AS _n FROM gold.{c['tabela']} c"
    if "bairro" in agrupar or "bairro" in filtros:
        sql += " JOIN gold.dim_bairro d ON d.id = c.bairro_id"
    if onde:
        sql += " WHERE " + " AND ".join(onde)
    if agrupar:
        sql += " GROUP BY " + ", ".join(col(d) for d in agrupar)
    corte = k_do_cubo(cubo) if c["minimo"] else 0
    # O maior primeiro, para o limite cortar só a cauda; tempo sai na ordem do calendário
    ordem = ", ".join(agrupar) if set(agrupar) & TEMPO or not agrupar else f"{medidas[0]} DESC NULLS LAST"
    try:
        linhas = [{k: (float(v) if hasattr(v, "as_tuple") else v) for k, v in r.items() if k != "_n"} for r in db.execute(text(
            f"SELECT * FROM ({sql}) g WHERE _n >= {corte} ORDER BY {ordem} LIMIT {LIMITE_LINHAS + 1}"), params).mappings()]
        ocultos = db.scalar(text(f"SELECT count(*) FROM ({sql}) g WHERE _n < {corte}"), params) or 0
    except ProgrammingError:
        db.rollback()
        return vazio
    r = {"linhas": linhas[:LIMITE_LINHAS], "ocultos": ocultos, "truncado": len(linhas) > LIMITE_LINHAS, "calculado": True}
    _cache[chave] = r
    while len(_cache) > CACHE_MAX:
        _cache.popitem(last=False)
    return r
