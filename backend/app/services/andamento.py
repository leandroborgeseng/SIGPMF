"""Tela de Conectores e Cargas (PR-B: CT5–CT12, CT16–CT22; UXC1–UXC13). Só LEITURA do Postgres (controle).

- Nunca toca a origem (SQL Server, API SIL, SEI): nada de conector aqui.
- Só nomes de etapa/subetapa/tabela, contagens e datas (CT12/CT19): nunca `mensagem` livre nem `str(exc)`.
- Consultas pequenas e limitadas: últimas 30 execuções por sistema, últimas 10 subetapas da execução mais recente.
"""
from __future__ import annotations

import statistics
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.controle import Agendamento, Execucao, ExecucaoSubetapa, Sistema
from app.services import progresso as pp
from app.services.subetapa import TextoInseguro, texto_seguro

HISTORICO = 30
LOG_SUBETAPAS = 10
SEM_SINAL_S = 300  # CT18: mais de 5 min sem sinal -> "pode estar travada"
ATIVOS = ("em_andamento", "rodando", "na_fila")


def _utc(d: datetime | None) -> datetime | None:
    if d is None:
        return None
    return d if d.tzinfo else d.replace(tzinfo=UTC)


def _iso(d: datetime | None) -> str | None:
    d = _utc(d)
    return d.isoformat() if d else None


def _texto(t: str | None) -> str | None:
    if not t:
        return None
    try:
        return texto_seguro(t)
    except TextoInseguro:
        return "Subetapa sem descrição"


def _tipo(ex: Execucao) -> str:
    if (ex.tipo or "").startswith("amostra"):
        return "Amostra"
    if ex.modo == "incremental":
        return "Incremental"
    return "Carga completa"


def _status(ex: Execucao) -> str:
    """na_fila | rodando | concluida | parcial | erro | cancelada | pausada"""
    s = ex.status or ""
    if s in ATIVOS:
        return "rodando"
    if s in ("sucesso", "sem_novidades"):
        return "concluida"
    if s == "parcial":
        return "parcial"
    if s in ("falha", "interrompida"):
        return "erro"
    return s or "na_fila"


def _proxima_acao(st: str) -> str | None:
    if st == "parcial":
        return "Continua na próxima agenda"
    if st == "erro":
        return "Rodar de novo"
    return None


def _duracao_tipica(db: Session, texto: str | None, ex_id: int) -> float | None:
    """UXC12: mediana (s) das últimas execuções da mesma subetapa, só das concluídas."""
    if not texto:
        return None
    rows = db.execute(select(ExecucaoSubetapa.iniciada_em, ExecucaoSubetapa.finalizada_em)
                      .where(ExecucaoSubetapa.subetapa == texto, ExecucaoSubetapa.execucao_id != ex_id,
                             ExecucaoSubetapa.finalizada_em.is_not(None))
                      .order_by(ExecucaoSubetapa.id.desc()).limit(10)).all()
    dur = [(_utc(f) - _utc(i)).total_seconds() for i, f in rows]
    return round(statistics.median(dur), 1) if dur else None


def _log(db: Session, ex: Execucao) -> list[dict]:
    fim_ex = _utc(ex.finalizado_em)
    out = []
    for s in db.scalars(select(ExecucaoSubetapa).where(ExecucaoSubetapa.execucao_id == ex.id)
                        .order_by(ExecucaoSubetapa.id.desc()).limit(LOG_SUBETAPAS)):
        fim = _utc(s.finalizada_em) or fim_ex
        out.append({"etapa": s.etapa, "subetapa": _texto(s.subetapa), "contador": s.contador, "total": s.total,
                    "iniciada_em": _iso(s.iniciada_em), "finalizada_em": _iso(fim),
                    "duracao_s": round((fim - _utc(s.iniciada_em)).total_seconds(), 1) if fim else None})
    return out


def _execucao(ex: Execucao, agora: datetime) -> dict:
    st = _status(ex)
    sinal = _utc(ex.ultimo_sinal_em) or _utc(ex.atualizado_em) or _utc(ex.iniciado_em)
    fim = _utc(ex.finalizado_em)
    return {
        "id": ex.id, "tipo": _tipo(ex), "status": st, "etapa": ex.etapa,
        "iniciado_em": _iso(ex.iniciado_em), "finalizado_em": _iso(fim),
        "duracao_s": round(((fim or agora) - _utc(ex.iniciado_em)).total_seconds(), 1) if ex.iniciado_em else None,
        "lidos": ex.lidos or 0, "gravados": (ex.novos or 0) + (ex.atualizados or 0), "quarentena": ex.pendentes or 0,
        "processadas_etapa": ex.processadas_etapa, "total_etapa": ex.total_etapa,
        "subetapa": _texto(ex.subetapa), "subetapa_contador": ex.subetapa_contador,
        "subetapa_total": ex.subetapa_total, "subetapa_iniciada_em": _iso(ex.subetapa_iniciada_em),
        "ultimo_sinal_em": _iso(sinal),
        "sem_sinal_s": round((agora - sinal).total_seconds(), 1) if (sinal and st == "rodando") else None,
        "motivo": pp.MOTIVOS.get(pp._motivo_fim(ex) or "") if st in ("erro", "parcial", "cancelada", "pausada")
        else None,
        "proxima_acao": _proxima_acao(st),
    }


def tela(db: Session, agora: datetime | None = None) -> dict:
    agora = agora or datetime.now(UTC)
    agendas = {a.conector.lower(): a for a in db.scalars(select(Agendamento))}
    cargas = []
    for s in db.scalars(select(Sistema).order_by(Sistema.nome)):
        exs = list(db.scalars(select(Execucao).where(Execucao.sistema == s.codigo)
                              .order_by(Execucao.id.desc()).limit(HISTORICO)))
        if not exs:
            continue
        atual = exs[0]
        d = _execucao(atual, agora)
        d["duracao_tipica_s"] = _duracao_tipica(db, atual.subetapa, atual.id) if d["status"] == "rodando" else None
        ult_comp = next((e for e in exs if _tipo(e) == "Carga completa" and _status(e) == "concluida"), None)
        ult_inc = next((e for e in exs if _tipo(e) == "Incremental" and _status(e) == "concluida"), None)
        ag = agendas.get(s.codigo.lower())
        cargas.append({
            "sistema": s.codigo, "fonte": s.nome, "atual": d, "log": _log(db, atual),
            "ultima_completa_em": _iso(ult_comp.finalizado_em) if ult_comp else None,
            "ultima_incremental_em": _iso(ult_inc.finalizado_em) if ult_inc else None,
            "agenda_ativa": bool(ag.ativo) if ag else None,
            "agenda_horario": ag.horario if (ag and ag.ativo) else None,
            "historico": [{k: v for k, v in _execucao(e, agora).items()
                           if k in ("id", "tipo", "status", "iniciado_em", "duracao_s", "lidos", "gravados")}
                          for e in exs],
        })
    ordem = {"rodando": 0, "erro": 1, "na_fila": 2, "parcial": 3, "concluida": 4}
    cargas.sort(key=lambda c: (ordem.get(c["atual"]["status"], 5), c["fonte"]))
    return {"agora": _iso(agora), "sem_sinal_limite_s": SEM_SINAL_S, "cargas": cargas}
