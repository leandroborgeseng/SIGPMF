"""Catálogo fechado. O modelo não escreve SQL: ou escolhe uma consulta agregada de carga, ou devolve uma consulta
estruturada (entidade, filtros, agrupamento, métrica, ordenação, limite). O backend valida contra a allowlist,
monta SELECT parametrizado e executa em transação somente leitura.

Folha vigente (`folha_senior`) é o último mês comum de silver.rh_remuneracao — a folha que o sistema mostra hoje.
O conector Senior (DBSENIOR) ainda não grava tabela própria. `folha_legada` é o histórico do mesmo portal.
CPF não é campo de nenhuma entidade.
"""
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import String, cast, func, select, text
from sqlalchemy.orm import Session

from app.models.camadas import DividaAtiva, EmpresaSil, Municipe
from app.models.controle import Execucao
from app.models.rh import MesRh, Remuneracao

# id da fonte (o que o modelo pode mandar) → domínio do ouro, para o mesmo recorte de secretaria dos painéis.
FONTES: dict[str, str] = {
    "sil": "tributario",
    "saude": "saude",
    "cemiterio": "cemiterio",
    "iss": "tributario",
    "iptu": "tributario",
    "divida": "tributario",
    "financeiro": "tributario",
    "educacao": "educacao",
    "sei": "sei",
}
FONTES_EMPRESA = frozenset({"sil", "iss"})
CONSULTAS = frozenset({"ultima_carga", "empresas_ultima_carga", "registros_por_fonte"})

_ALIASES = {
    "sil": "sil", "api sil": "sil", "api_sil": "sil",
    "saude": "saude", "saúde": "saude",
    "cemiterio": "cemiterio", "cemitério": "cemiterio",
    "iss": "iss",
    "iptu": "iptu",
    "divida": "divida", "dívida": "divida", "divida ativa": "divida", "dívida ativa": "divida",
    "financeiro": "financeiro",
    "educacao": "educacao", "educação": "educacao", "gestaoescolar": "educacao",
    "sei": "sei",
}


class ForaDoCatalogo(Exception):
    """Nome ou parâmetro que não está na allowlist. `bruto` é o que o modelo devolveu, se chegou a ser JSON."""

    def __init__(self, bruto: dict | None = None):
        self.bruto = bruto


@dataclass(frozen=True)
class Agregado:
    estado: str  # ok | sem_carga
    consulta: str
    fonte_id: str
    rotulo: str
    filtros: dict
    carga_em: str | None
    status_carga: str | None
    numeros: dict | None


def _norm_fonte(valor) -> str | None:
    if not isinstance(valor, str):
        return None
    return _ALIASES.get(" ".join(valor.strip().lower().split()))


def validar(escolha) -> tuple[str, dict]:
    """Allowlist: nome da consulta, só o parâmetro `fonte`, e o valor dentro do enum daquela consulta."""
    if not isinstance(escolha, dict):
        raise ForaDoCatalogo()
    nome = escolha.get("consulta")
    params = escolha.get("parametros")
    if nome not in CONSULTAS or not isinstance(params, dict) or set(params) != {"fonte"}:
        raise ForaDoCatalogo(escolha)
    fonte = _norm_fonte(params.get("fonte"))
    permitidas = FONTES_EMPRESA if nome == "empresas_ultima_carga" else FONTES.keys()
    if fonte not in permitidas:
        raise ForaDoCatalogo(escolha)
    return nome, {"fonte": fonte}


def _progresso(fonte_id: str):
    from app.services.progresso import fontes
    f = next((x for x in fontes() if x.id == fonte_id), None)
    if f is None:
        raise ForaDoCatalogo()
    return f


def ultima_execucao(db: Session, fonte_id: str) -> Execucao | None:
    """Última execução da fonte, qualquer status. Reusa o mesmo critério do painel de progresso."""
    from app.services.progresso import _ultima
    return _ultima(db, _progresso(fonte_id))


def _iso(d: datetime | None) -> str | None:
    if d is None:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=UTC)
    return d.isoformat()


def _base(consulta: str, fonte_id: str, ex: Execucao | None, estado: str, numeros: dict | None) -> Agregado:
    prog = _progresso(fonte_id)
    return Agregado(estado, consulta, fonte_id, prog.nome, {"fonte": prog.nome},
                    _iso(ex.finalizado_em or ex.iniciado_em) if ex else None,
                    ex.status if ex else None, numeros)


def _ultima(db: Session, fonte_id: str) -> Agregado:
    ex = ultima_execucao(db, fonte_id)
    if ex is None:
        return _base("ultima_carga", fonte_id, None, "sem_carga", None)
    numeros = {"lidos": int(ex.lidos or 0), "novos": int(ex.novos or 0),
               "atualizados": int(ex.atualizados or 0), "ignorados": int(ex.ignorados or 0)}
    return _base("ultima_carga", fonte_id, ex, "ok", numeros)


def _empresas(db: Session, fonte_id: str) -> Agregado:
    ex = ultima_execucao(db, fonte_id)
    if ex is None:
        return _base("empresas_ultima_carga", fonte_id, None, "sem_carga", None)
    if fonte_id == "sil":
        n = db.scalar(select(func.count()).select_from(EmpresaSil)) or 0
    else:  # iss: só o tipo empresa (a prata do Munícipe). Nunca a linha, só o COUNT.
        n = db.scalar(select(func.count()).select_from(Municipe).where(Municipe.tipo == "empresa")) or 0
    return _base("empresas_ultima_carga", fonte_id, ex, "ok", {"empresas": int(n)})


def _registros(db: Session, fonte_id: str) -> Agregado:
    """Mesmas contagens do card da base (bronze/prata/ouro/pendentes), pelo retrato que a tela já usa."""
    from app.services.contagens_cache import obter
    from app.services.indicadores import contagens_por_sistema
    cont, _atualizando = obter(db, "conectores", contagens_por_sistema)
    if cont is None:
        cont = contagens_por_sistema(db)
    sistema = _progresso(fonte_id).sistemas[0]
    numeros = {k: int((cont.get(k) or {}).get(sistema, 0)) for k in ("bronze", "prata", "ouro", "pendentes")}
    ex = ultima_execucao(db, fonte_id)
    if ex is None and not any(numeros.values()):
        return _base("registros_por_fonte", fonte_id, None, "sem_carga", None)
    return _base("registros_por_fonte", fonte_id, ex, "ok", numeros)


_EXEC = {"ultima_carga": _ultima, "empresas_ultima_carga": _empresas, "registros_por_fonte": _registros}


def executar(db: Session, consulta: str, params: dict) -> Agregado:
    return _EXEC[consulta](db, params["fonte"])


# --- Catálogo semântico: o modelo descreve a consulta; o backend monta o SELECT. ---

OPERADORES = ("eq", "ne", "gt", "gte", "lt", "lte")
METRICAS = ("max", "min", "soma", "media", "contagem")
DIRECOES = ("asc", "desc")
LIMITE_MAX = 50
LIMITE_PADRAO = 20
TIMEOUT = "8s"
# Código de controle.secretarias → nome em silver.rh_remuneracao (app/silver/rh.py).
FOLHA_POR_CODIGO = {
    "EDUCACAO": "Educação", "SAUDE": "Saúde", "FAZENDA": "Finanças",
    "ASSISTENCIA_SOCIAL": "Ação Social", "ADMINISTRACAO": "Administração e RH",
    "SERVICOS_URBANOS": "Meio Ambiente e Serviços",
}
_ALIAS_ENT = {
    "folha_senior": "folha_senior", "senior": "folha_senior", "folha vigente": "folha_senior", "folha": "folha_senior",
    "folha_legada": "folha_legada", "legada": "folha_legada", "historico": "folha_legada", "histórico": "folha_legada",
    "empresas": "empresas", "empresa": "empresas",
    "divida": "divida", "dívida": "divida", "divida ativa": "divida", "dívida ativa": "divida",
}


@dataclass(frozen=True)
class Campo:
    nome: str
    significado: str
    tipo: str  # texto | inteiro | numero | bool
    coluna: object
    filtro: bool = True
    agrupavel: bool = False
    metrica: bool = False
    nominal: bool = False


@dataclass(frozen=True)
class Entidade:
    id: str
    rotulo: str
    significado: str
    modelo: type
    campos: dict[str, Campo]
    folha: bool = False
    vigente: bool = False
    fonte_id: str | None = None
    sistema: str | None = None
    secretaria: str | None = None


def _folha() -> dict[str, Campo]:
    r = Remuneracao
    return {
        "ano": Campo("ano", "Ano da competência.", "inteiro", r.ano, agrupavel=True),
        "mes": Campo("mes", "Mês da competência; 13 é o décimo terceiro.", "inteiro", r.mes, agrupavel=True),
        "nome": Campo("nome", "Nome da pessoa. Só sai para quem já vê a folha daquela secretaria.", "texto", r.nome, nominal=True),
        "cargo": Campo("cargo", "Cargo na competência.", "texto", r.cargo, agrupavel=True),
        "secretaria": Campo("secretaria", "Secretaria padronizada (a mesma do painel de RH).", "texto", r.secretaria, agrupavel=True),
        "grupo": Campo("grupo", "ativa (secretarias), inativos ou outros.", "texto", r.grupo, agrupavel=True),
        "situacao": Campo("situacao", "Situação funcional publicada.", "texto", r.situacao, agrupavel=True),
        "salario_base": Campo("salario_base", "Salário base do mês.", "numero", r.salario_base, metrica=True),
        "vencimentos": Campo("vencimentos", "Total de vencimentos (bruto) de quem foi pago no mês.", "numero", r.vencimentos, metrica=True),
        "descontos": Campo("descontos", "Total de descontos do mês.", "numero", r.descontos, metrica=True),
        "liquido": Campo("liquido", "Total líquido do mês.", "numero", r.liquido, metrica=True),
    }


def _empresas() -> dict[str, Campo]:
    e = EmpresaSil
    return {
        "nome": Campo("nome", "Nome ou razão social, sem documento.", "texto", e.nome, agrupavel=True),
        "tipo_pessoa": Campo("tipo_pessoa", "PF ou PJ, sem CPF e sem CNPJ.", "texto", e.tipo_pessoa, agrupavel=True),
        "possui_debito": Campo("possui_debito", "Se a empresa está marcada com débito.", "bool", e.possui_debito, agrupavel=True),
        "bairro": Campo("bairro", "Bairro do estabelecimento.", "texto", e.bairro, agrupavel=True),
    }


def _divida() -> dict[str, Campo]:
    d = DividaAtiva
    return {
        "ano": Campo("ano", "Ano da inscrição.", "inteiro", d.ano, agrupavel=True),
        "natureza": Campo("natureza", "Código da natureza do débito.", "inteiro", d.natureza, agrupavel=True),
        "saldo": Campo("saldo", "Saldo líquido em aberto.", "numero", d.saldo, metrica=True),
        "em_aberto": Campo("em_aberto", "Débito em aberto (não extinto, saldo positivo).", "bool", d.em_aberto, agrupavel=True),
        "extinta": Campo("extinta", "Inscrição extinta.", "bool", d.extinta, agrupavel=True),
        "protestos": Campo("protestos", "Quantidade de protestos.", "inteiro", d.protestos, metrica=True, agrupavel=True),
    }


def _entidades() -> dict[str, Entidade]:
    folha = _folha()
    return {
        "folha_senior": Entidade(
            "folha_senior", "Folha vigente",
            "Folha vigente: último mês comum publicado no portal. O Senior ainda não tem tabela; esta é a folha corrente.",
            Remuneracao, folha, folha=True, vigente=True, sistema="PORTAL_RH", secretaria="secretaria"),
        "folha_legada": Entidade(
            "folha_legada", "Folha (histórico)",
            "Histórico da folha publicada no portal, todos os meses, inclusive o 13º.",
            Remuneracao, folha, folha=True, sistema="PORTAL_RH", secretaria="secretaria"),
        "empresas": Entidade(
            "empresas", "Empresas (SIL)",
            "Empresas da prata da SIL. Sem CPF, sem CNPJ e sem endereço completo.",
            EmpresaSil, _empresas(), fonte_id="sil"),
        "divida": Entidade(
            "divida", "Dívida ativa",
            "Inscrições em dívida ativa. Sem contribuinte, sem CPF e sem inscrição de imóvel.",
            DividaAtiva, _divida(), fonte_id="divida"),
    }


ENTIDADES: dict[str, Entidade] = _entidades()


@dataclass(frozen=True)
class Filtro:
    campo: str
    operador: str
    valor: object
    como_texto: bool = False


@dataclass(frozen=True)
class Metrica:
    funcao: str
    campo: str | None = None


@dataclass(frozen=True)
class Ordem:
    campo: str
    direcao: str


@dataclass(frozen=True)
class ConsultaEstruturada:
    entidade: str
    filtros: tuple[Filtro, ...] = ()
    agrupamento: tuple[str, ...] = ()
    metrica: Metrica | None = None
    ordenacao: tuple[Ordem, ...] = ()
    limite: int = LIMITE_PADRAO


@dataclass
class ResultadoConsulta:
    estado: str
    consulta: str
    fonte: str
    filtros: dict
    carga_em: str | None
    status_carga: str | None
    numeros: dict | None
    linhas: list
    metrica: str | None
    nominal: bool


def resumo_para_modelo() -> str:
    """Texto curto do catálogo para o prompt. Sem CPF e sem nome de coluna física."""
    partes = []
    for e in ENTIDADES.values():
        partes.append(f"{e.id} ({e.significado}) campos: {', '.join(e.campos)}.")
    return " ".join(partes)


def _norm_ent(valor) -> str | None:
    if not isinstance(valor, str):
        return None
    return _ALIAS_ENT.get(" ".join(valor.strip().lower().split()))


def _canon_secretaria(valor: str) -> str:
    from app.silver.rh import SECRETARIAS, secretaria
    nome = secretaria(valor)
    return nome if nome in {n for _, n in SECRETARIAS} else valor[:200]


def _inteiro(valor) -> int | None:
    if isinstance(valor, bool):
        return None
    if isinstance(valor, int):
        return valor
    if isinstance(valor, float) and valor.is_integer():
        return int(valor)
    if isinstance(valor, str) and re.fullmatch(r"-?\d+", valor.strip()):
        return int(valor.strip())
    return None


def _numero(valor) -> float | None:
    if isinstance(valor, float) and not isinstance(valor, bool):
        return float(valor)
    n = _inteiro(valor)
    if n is not None:
        return float(n)
    if isinstance(valor, str) and re.fullmatch(r"-?\d+(\.\d+)?", valor.strip()):
        return float(valor.strip())
    return None


def _coerce(campo: Campo, valor) -> tuple[object, bool]:
    """Devolve (valor, como_texto). Texto que não casa com o tipo fica bound como texto e não entra no SQL."""
    if campo.tipo == "texto":
        if isinstance(valor, str):
            bruto = valor[:200]
        elif isinstance(valor, (int, float)) and not isinstance(valor, bool):
            bruto = str(valor)[:200]
        else:
            raise ForaDoCatalogo()
        if campo.nome == "secretaria":
            bruto = _canon_secretaria(bruto)
        return bruto, False
    if campo.tipo == "bool":
        if isinstance(valor, bool):
            return valor, False
        if isinstance(valor, str) and valor.strip().lower() in ("true", "false", "sim", "nao", "não"):
            return valor.strip().lower() in ("true", "sim"), False
        if isinstance(valor, str):
            return valor[:200], True
        raise ForaDoCatalogo()
    if isinstance(valor, str) and (campo.tipo == "inteiro" and _inteiro(valor) is None or campo.tipo == "numero" and _numero(valor) is None):
        return valor[:200], True
    if campo.tipo == "inteiro":
        n = _inteiro(valor)
        if n is None:
            raise ForaDoCatalogo()
        return n, False
    n = _numero(valor)
    if n is None:
        raise ForaDoCatalogo()
    return n, False


def _limite(valor) -> int:
    if valor is None:
        return LIMITE_PADRAO
    n = _inteiro(valor)
    if n is None or n < 1:
        raise ForaDoCatalogo()
    return min(n, LIMITE_MAX)


def consulta_nominal(c: ConsultaEstruturada) -> bool:
    """max/min ou lista sem agrupamento identificam uma pessoa. Agregado (soma, média, contagem ou grupo) não."""
    ent = ENTIDADES[c.entidade]
    if not ent.folha:
        return False
    usados = [f.campo for f in c.filtros] + list(c.agrupamento) + [o.campo for o in c.ordenacao]
    if c.metrica and c.metrica.campo:
        usados.append(c.metrica.campo)
    if any(ent.campos[u].nominal for u in usados if u in ent.campos):
        return True
    if c.agrupamento:
        return False
    if c.metrica and c.metrica.funcao in ("soma", "media", "contagem"):
        return False
    return True


def secretaria_fora_do_escopo(c: ConsultaEstruturada, nomes: list[str] | None) -> bool:
    if nomes is None:
        return False
    return any(f.campo == "secretaria" and f.operador == "eq" and str(f.valor) not in nomes for f in c.filtros)


def validar_estruturada(escolha) -> ConsultaEstruturada:
    if not isinstance(escolha, dict):
        raise ForaDoCatalogo()
    ent_id = _norm_ent(escolha.get("entidade"))
    ent = ENTIDADES.get(ent_id or "")
    if ent is None:
        raise ForaDoCatalogo(escolha)
    filtros_in = escolha.get("filtros") or []
    if not isinstance(filtros_in, list):
        raise ForaDoCatalogo(escolha)
    filtros: list[Filtro] = []
    for item in filtros_in:
        if not isinstance(item, dict) or not {"campo", "operador", "valor"} <= set(item):
            raise ForaDoCatalogo(escolha)
        op = item.get("operador")
        op = op.strip().lower() if isinstance(op, str) else op
        campo = ent.campos.get(item.get("campo"))
        if campo is None or not campo.filtro or op not in OPERADORES:
            raise ForaDoCatalogo(escolha)
        try:
            valor, como_texto = _coerce(campo, item.get("valor"))
        except ForaDoCatalogo:
            raise ForaDoCatalogo(escolha) from None
        filtros.append(Filtro(campo.nome, op, valor, como_texto))
    agrup_in = escolha.get("agrupamento") or []
    if not isinstance(agrup_in, list) or any(a not in ent.campos or not ent.campos[a].agrupavel for a in agrup_in):
        raise ForaDoCatalogo(escolha)
    agrup = tuple(agrup_in)
    met = _metrica(escolha.get("metrica"), ent, escolha)
    ordem = _ordem(escolha.get("ordenacao"), ent, agrup, met, escolha)
    try:
        limite = _limite(escolha.get("limite"))
    except ForaDoCatalogo:
        raise ForaDoCatalogo(escolha) from None
    return ConsultaEstruturada(ent.id, tuple(filtros), agrup, met, ordem, limite)


def _metrica(bruto, ent: Entidade, escolha) -> Metrica | None:
    if bruto is None:
        return None
    if not isinstance(bruto, dict) or bruto.get("funcao") not in METRICAS:
        raise ForaDoCatalogo(escolha)
    fn = bruto["funcao"]
    campo = bruto.get("campo")
    if fn == "contagem" and campo in (None, "", "*"):
        return Metrica(fn, None)
    col = ent.campos.get(campo)
    if col is None or (fn != "contagem" and not col.metrica):
        raise ForaDoCatalogo(escolha)
    return Metrica(fn, col.nome)


def _ordem(bruto, ent: Entidade, agrup: tuple[str, ...], met: Metrica | None, escolha) -> tuple[Ordem, ...]:
    if bruto is None:
        bruto = []
    if not isinstance(bruto, list):
        raise ForaDoCatalogo(escolha)
    saida: list[Ordem] = []
    for item in bruto:
        if not isinstance(item, dict):
            raise ForaDoCatalogo(escolha)
        campo = item.get("campo")
        direcao = item.get("direcao") or "asc"
        direcao = direcao.strip().lower() if isinstance(direcao, str) else direcao
        if direcao not in DIRECOES or not _pode_ordenar(campo, ent, agrup, met):
            raise ForaDoCatalogo(escolha)
        saida.append(Ordem(campo, direcao))
    return tuple(saida)


def _pode_ordenar(campo, ent: Entidade, agrup: tuple[str, ...], met: Metrica | None) -> bool:
    if campo == "valor":
        return met is not None
    if campo not in ent.campos:
        return False
    if agrup:
        return campo in agrup or (met is not None and campo == met.campo)
    if met and met.funcao in ("soma", "media", "contagem"):
        return campo == met.campo
    return True


def _predicado(col, f: Filtro):
    c = cast(col, String) if f.como_texto else col
    return {"eq": c == f.valor, "ne": c != f.valor, "gt": c > f.valor, "gte": c >= f.valor,
            "lt": c < f.valor, "lte": c <= f.valor}[f.operador]


def _expr_metrica(ent: Entidade, met: Metrica):
    if met.funcao == "contagem" and not met.campo:
        return func.count().label("contagem")
    col = ent.campos[met.campo].coluna
    fn = {"max": func.max, "min": func.min, "soma": func.sum, "media": func.avg, "contagem": func.count}[met.funcao]
    rotulo = "contagem" if met.funcao == "contagem" else met.campo
    return fn(col).label(rotulo)


def _colunas_pessoa(ent: Entidade, extra: str | None) -> list:
    nomes = ["nome"]
    if ent.secretaria:
        nomes.append(ent.secretaria)
    if extra and extra not in nomes:
        nomes.append(extra)
    elif "vencimentos" in ent.campos and "vencimentos" not in nomes and extra is None:
        nomes.append("vencimentos")
    return [ent.campos[n].coluna for n in nomes]


def montar(c: ConsultaEstruturada, conds: list):
    """SELECT só com colunas do catálogo. Valores ficam em bind param, nunca interpolados."""
    ent = ENTIDADES[c.entidade]
    met = c.metrica
    if c.agrupamento:
        cols = [ent.campos[a].coluna for a in c.agrupamento]
        valor = _expr_metrica(ent, met) if met else func.count().label("contagem")
        q = select(*cols, valor).where(*conds).group_by(*cols)
    elif met and met.funcao in ("max", "min") and consulta_nominal(c):
        col = ent.campos[met.campo].coluna
        extremo = select(func.max(col) if met.funcao == "max" else func.min(col)).where(*conds).scalar_subquery()
        q = select(*_colunas_pessoa(ent, met.campo)).where(*conds, col == extremo)
    elif met:
        q = select(_expr_metrica(ent, met)).where(*conds)
    elif consulta_nominal(c):
        q = select(*_colunas_pessoa(ent, None)).where(*conds)
    else:
        nomes = [n for n, col in ent.campos.items() if not col.nominal][:6]
        q = select(*[ent.campos[n].coluna for n in nomes]).where(*conds)
    agrega = bool(c.agrupamento) or (met is not None and not (met.funcao in ("max", "min") and consulta_nominal(c)))
    for o in c.ordenacao:
        expr = _expr_ordem(ent, met, o.campo, agrega)
        q = q.order_by(expr.desc() if o.direcao == "desc" else expr.asc())
    return q.limit(c.limite)


def _expr_ordem(ent: Entidade, met: Metrica | None, campo: str, agrega: bool):
    if campo == "valor":
        if agrega and met is not None:
            return _expr_metrica(ent, met)
        return ent.campos[met.campo].coluna
    if agrega and met is not None and campo == met.campo:
        return _expr_metrica(ent, met)
    return ent.campos[campo].coluna


def _conds(c: ConsultaEstruturada, *, ano: int | None, mes: int | None, secretarias: list[str] | None) -> list:
    ent = ENTIDADES[c.entidade]
    conds = [_predicado(ent.campos[f.campo].coluna, f) for f in c.filtros]
    if ent.folha:
        conds.append(Remuneracao.vencimentos > 0)
        if ent.vigente:
            conds.append(Remuneracao.ano == ano)
            conds.append(Remuneracao.mes == mes)
        if secretarias is not None:
            conds.append(Remuneracao.secretaria.in_(secretarias))
    return conds


def ler(db: Session, stmt) -> list[dict]:
    """Outra conexão, transação READ ONLY, timeout de statement, rollback no fim. Não usa a sessão da auditoria."""
    with db.get_bind().connect() as conn:
        tx = conn.begin()
        try:
            conn.execute(text("SET TRANSACTION READ ONLY"))
            conn.execute(text(f"SET LOCAL statement_timeout = '{TIMEOUT}'"))
            return [_linha(dict(r._mapping)) for r in conn.execute(stmt)]
        finally:
            tx.rollback()


def _linha(row: dict) -> dict:
    out = {}
    for k, v in row.items():
        if isinstance(v, Decimal):
            v = round(float(v), 2)
        elif isinstance(v, datetime):
            v = _iso(v)
        out[k] = v
    return out


def _mes_vigente(db: Session) -> tuple[int, int] | None:
    stmt = (select(Remuneracao.ano, Remuneracao.mes)
            .where(Remuneracao.mes != 13, Remuneracao.vencimentos > 0)
            .order_by(Remuneracao.ano.desc(), Remuneracao.mes.desc()).limit(1))
    rows = ler(db, stmt)
    if not rows:
        return None
    return int(rows[0]["ano"]), int(rows[0]["mes"])


def _numeros(linhas: list[dict], met: Metrica | None) -> dict | None:
    if len(linhas) != 1 or met is None:
        return None
    chave = "contagem" if met.funcao == "contagem" else met.campo
    valor = linhas[0].get(chave)
    if isinstance(valor, bool) or not isinstance(valor, (int, float)):
        return None
    return {chave: valor}


def _filtros_publicos(c: ConsultaEstruturada, extras: dict) -> dict:
    out = {f"{f.campo} {f.operador}": _publico(f.valor) for f in c.filtros}
    out.update({k: _publico(v) for k, v in extras.items()})
    return out


def _publico(v) -> str:
    s = " ".join(str(v).split())[:80]
    s = re.sub(r"\b\d{3}\.\d{3}\.\d{3}-\d{2}\b", "[removido]", s)
    return re.sub(r"\b\d{11}\b", "[removido]", s)


def _carga(db: Session, ent: Entidade) -> tuple[str | None, str | None]:
    if ent.folha:
        quando = db.scalar(select(func.max(MesRh.carregado_em)))
        ex = db.scalar(select(Execucao).where(Execucao.sistema == ent.sistema).order_by(Execucao.id.desc()).limit(1))
        return _iso(quando), (ex.status if ex else None)
    if not ent.fonte_id:
        return None, None
    try:
        ex = ultima_execucao(db, ent.fonte_id)
    except ForaDoCatalogo:
        return None, None
    if ex is None:
        return None, None
    return _iso(ex.finalizado_em or ex.iniciado_em), ex.status


def executar_estruturada(db: Session, c: ConsultaEstruturada, *, secretarias: list[str] | None) -> ResultadoConsulta:
    ent = ENTIDADES[c.entidade]
    ano = mes = None
    if ent.vigente:
        achado = _mes_vigente(db)
        if achado is None:
            return _vazio(c, ent, "sem_carga", {}, None, None)
        ano, mes = achado
    extras = {}
    if ano is not None:
        extras["ano"] = ano
        extras["mes"] = mes
    if secretarias is not None:
        extras["secretaria"] = ", ".join(secretarias)
    filtros = _filtros_publicos(c, extras)
    base = _conds(ConsultaEstruturada(c.entidade), ano=ano, mes=mes, secretarias=None)
    n = ler(db, select(func.count().label("n")).select_from(ent.modelo).where(*base))
    total = int(next(iter(n[0].values()))) if n else 0
    if not total:
        return _vazio(c, ent, "sem_carga", filtros, *_carga(db, ent))
    conds = _conds(c, ano=ano, mes=mes, secretarias=secretarias)
    linhas = ler(db, montar(c, conds))
    carga_em, status = _carga(db, ent)
    met = f"{c.metrica.funcao}:{c.metrica.campo or 'contagem'}" if c.metrica else None
    return ResultadoConsulta("ok", ent.id, ent.rotulo, filtros, carga_em, status, _numeros(linhas, c.metrica),
                             linhas, met, consulta_nominal(c))


def _vazio(c, ent: Entidade, estado: str, filtros: dict, carga_em, status) -> ResultadoConsulta:
    return ResultadoConsulta(estado, ent.id, ent.rotulo, filtros, carga_em, status, None, [], None, consulta_nominal(c))
