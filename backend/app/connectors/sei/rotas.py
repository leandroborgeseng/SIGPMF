"""Rotas do conector SEI. Identificadores vão no CORPO, nunca na URL; motivo do revelar também.

- GET  /api/sei/status                     admin: o que falta configurar (só nomes) e ambiente_origem.
- POST /api/sei/amostra                    admin: amostra controlada (SEI3). Inerte -> 409.
- POST /api/sei/incremental                admin: andamentos (SEI4). Inerte, sem liberação, sem amostra ou fora da
                                           janela -> 409.
- POST /api/sei/interessados               admin/admin_lgpd/operador: interessados de um processo, mascarados.
- POST /api/sei/interessados/revelar       só admin_lgpd, motivo obrigatório, auditado (SEI5).
"""
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.connectors.sei import carga
from app.connectors.sei.cliente import SeiDesabilitado
from app.connectors.sei.config import ConfigSei
from app.connectors.sei.projecao import PERFIS_REVELAM, visao_interessados
from app.core.audit import audita
from app.core.config import get_settings
from app.core.db import get_db
from app.core.security import ADMIN, usuario_atual
from app.models.camadas import RegistroBronze
from app.models.controle import Usuario
from app.services.acesso_secretaria import dom as _dom_pr06  # noqa: E402

sei = APIRouter(prefix="/sei", tags=["sei"], dependencies=[Depends(usuario_atual)])
PERFIS_CONSULTA = ("admin", "admin_lgpd", "operador")


def _ip(req: Request) -> str | None:
    return req.client.host if req.client else None


def _ex(ex) -> dict:
    return {"execucao_id": ex.id, "status": ex.status, "ambiente_origem": ex.ambiente_origem, "lidos": ex.lidos,
            "novos": ex.novos, "mensagem": ex.mensagem}


@sei.get("/status", dependencies=[Depends(ADMIN)])
def status():
    return ConfigSei.do_ambiente().resumo_publico()


@sei.post("/amostra", dependencies=[Depends(ADMIN)])
def amostra(req: Request, u: Usuario = Depends(usuario_atual), db: Session = Depends(get_db)):
    try:
        ex = carga.executar_amostra(db, u.email)
    except SeiDesabilitado as exc:
        audita(db, u.email, "sei_negado", "conector:sei:amostra", {"motivo": "inerte"}, _ip(req))
        raise HTTPException(409, str(exc)) from None
    return _ex(ex)


@sei.post("/incremental", dependencies=[Depends(ADMIN)])
def incremental(req: Request, u: Usuario = Depends(usuario_atual), db: Session = Depends(get_db)):
    try:
        ex = carga.executar_incremental(db, u.email, datetime.now(UTC))
    except (SeiDesabilitado, carga.CargaRecusada) as exc:
        audita(db, u.email, "sei_negado", "conector:sei:incremental", {"motivo": str(exc)}, _ip(req))
        raise HTTPException(409, str(exc)) from None
    return _ex(ex)


class ProcessoIn(BaseModel):
    id_procedimento: str = Field(..., min_length=1, max_length=40)


class RevelarIn(ProcessoIn):
    motivo: str | None = Field(None, max_length=500)


def _interessados(db: Session, id_proc: str) -> list[dict]:
    r = db.scalar(select(RegistroBronze).where(RegistroBronze.sistema_origem == carga.SISTEMA,
                                               RegistroBronze.tabela == carga.TAB_PROCESSOS,
                                               RegistroBronze.id_origem == id_proc)
                  .order_by(RegistroBronze.id.desc()).limit(1))
    if r is None:
        raise HTTPException(404, "Processo não encontrado")
    return list((r.dados or {}).get("interessados") or [])


@sei.post("/interessados", dependencies=[Depends(_dom_pr06("sei"))])
def interessados(dados: ProcessoIn, req: Request, u: Usuario = Depends(usuario_atual), db: Session = Depends(get_db)):
    if u.perfil not in PERFIS_CONSULTA:
        audita(db, u.email, "sei_interessados_negado", "sei:processo", {"perfil": u.perfil}, _ip(req))
        raise HTTPException(403, "Perfil sem permissão")
    return {"interessados": visao_interessados(_interessados(db, dados.id_procedimento), u.perfil)}


@sei.post("/interessados/revelar", dependencies=[Depends(_dom_pr06("sei"))])
def revelar(dados: RevelarIn, req: Request, u: Usuario = Depends(usuario_atual), db: Session = Depends(get_db)):
    alvo = f"sei:processo:{dados.id_procedimento}"
    if u.perfil not in PERFIS_REVELAM:
        audita(db, u.email, "revelar_sei_negado", alvo, {"perfil": u.perfil}, _ip(req))
        raise HTTPException(403, "Só o perfil admin_lgpd revela interessados do SEI")
    motivo = (dados.motivo or "").strip()
    if len(motivo) < get_settings().revelar_motivo_min:
        audita(db, u.email, "revelar_sei_negado", alvo, {"perfil": u.perfil, "razao": "motivo_invalido"}, _ip(req))
        raise HTTPException(400, "Informe o motivo para revelar os interessados.")
    lst = _interessados(db, dados.id_procedimento)
    audita(db, u.email, "revelar_sei_interessados", alvo, {"motivo": motivo[:300], "interessados": len(lst)}, _ip(req))
    return {"interessados": visao_interessados(lst, u.perfil, revela=True),
            "remascarar_em_segundos": get_settings().revelar_cpf_segundos}
