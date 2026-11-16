"""Catálogo fechado de consultas agregadas. O modelo só escolhe o nome e os parâmetros; o backend valida e executa.

Nada aqui lê linha crua nem dado pessoal: só contagens e a data da última carga, reusando o painel de progresso,
as contagens por camada e a prata já agregada (empresas).
"""
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.camadas import EmpresaSil, Municipe
from app.models.controle import Execucao

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
