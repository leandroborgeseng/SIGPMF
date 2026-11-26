"""Rotas do bloco Processos SEI nas fichas. O id na URL é o uuid da ficha, nunca CPF ou CNPJ.

- GET  /gold/cidadaos/{cid}/sei                         processos do interessado (CPF, peso 1.0)
- POST /gold/cidadaos/{cid}/sei/interessados            outros interessados, mascarados
- POST /gold/cidadaos/{cid}/sei/interessados/revelar    só admin_lgpd, com motivo, auditado
- GET  /gold/empresas/{empresa_id}/sei                  o mesmo, pelo CNPJ do interessado
- POST /gold/empresas/{empresa_id}/sei/interessados
- POST /gold/empresas/{empresa_id}/sei/interessados/revelar
"""
import re

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.connectors.sei.projecao import PERFIS_REVELAM
from app.core.audit import audita
from app.core.config import get_settings
from app.core.db import get_db
from app.core.security import usuario_atual
from app.models.controle import Usuario
from app.services import sei_ficha
from app.services.acesso_secretaria import dom as _dom_sei

ficha_sei = APIRouter(tags=["sei-ficha"], dependencies=[Depends(usuario_atual), Depends(_dom_sei("sei"))])
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


class InteressadosIn(BaseModel):
    id_procedimento: str = Field(..., min_length=1, max_length=40)


class RevelarIn(InteressadosIn):
    motivo: str | None = Field(None, max_length=500)


def _ip(req: Request) -> str | None:
    return req.client.host if req.client else None


def _id(valor: str) -> str:
    if not _UUID.match(valor or ""):
        raise HTTPException(404, "Ficha não encontrada")
    return valor


def _resposta(db: Session, u: Usuario, req: Request, alvo: str, campo: str, valor: str) -> dict:
    r = sei_ficha.listar(db, campo=campo, valor=valor)
    audita(db, u.email, "consulta_sei_ficha", alvo,
           {"fonte_liberada": r["fonte_liberada"], "total": r["total"]}, _ip(req))
    return {"ficha_id": valor, **r}


def _lista_outros(db: Session, u: Usuario, req: Request, alvo: str, campo: str, valor: str, idp: str) -> dict:
    if not sei_ficha.fonte_liberada():
        raise HTTPException(409, sei_ficha.MSG_FONTE)
    lst = sei_ficha.interessados_do_processo(db, campo=campo, valor=valor, id_procedimento=idp)
    if lst is None:
        raise HTTPException(404, "Processo não encontrado")
    audita(db, u.email, "consulta_sei_outros", alvo, {"interessados": len(lst)}, _ip(req))
    return {"interessados": sei_ficha.visao_outros(lst, revela=False)}


def _revela(db: Session, u: Usuario, req: Request, alvo: str, campo: str, valor: str, dados: RevelarIn) -> dict:
    if u.perfil not in PERFIS_REVELAM:
        audita(db, u.email, "revelar_sei_ficha_negado", alvo, {"perfil": u.perfil}, _ip(req))
        raise HTTPException(403, "Só o perfil admin_lgpd revela interessados do SEI")
    motivo = (dados.motivo or "").strip()
    if len(motivo) < get_settings().revelar_motivo_min:
        audita(db, u.email, "revelar_sei_ficha_negado", alvo, {"razao": "motivo_invalido"}, _ip(req))
        raise HTTPException(400, "Informe o motivo para revelar os interessados.")
    if not sei_ficha.fonte_liberada():
        raise HTTPException(409, sei_ficha.MSG_FONTE)
    lst = sei_ficha.interessados_do_processo(db, campo=campo, valor=valor, id_procedimento=dados.id_procedimento)
    if lst is None:
        raise HTTPException(404, "Processo não encontrado")
    audita(db, u.email, "revelar_sei_ficha_interessados", alvo,
           {"motivo": motivo[:300], "interessados": len(lst)}, _ip(req))
    return {"interessados": sei_ficha.visao_outros(lst, revela=True),
            "remascarar_em_segundos": get_settings().revelar_cpf_segundos}


@ficha_sei.get("/gold/cidadaos/{cid}/sei")
def sei_do_cidadao(cid: str, req: Request, u: Usuario = Depends(usuario_atual), db: Session = Depends(get_db)):
    cid = _id(cid)
    return _resposta(db, u, req, f"cidadao:{cid}", "cidadao_id", cid)


@ficha_sei.post("/gold/cidadaos/{cid}/sei/interessados")
def outros_do_cidadao(cid: str, dados: InteressadosIn, req: Request, u: Usuario = Depends(usuario_atual),
                      db: Session = Depends(get_db)):
    cid = _id(cid)
    return _lista_outros(db, u, req, f"cidadao:{cid}", "cidadao_id", cid, dados.id_procedimento)


@ficha_sei.post("/gold/cidadaos/{cid}/sei/interessados/revelar")
def revelar_do_cidadao(cid: str, dados: RevelarIn, req: Request, u: Usuario = Depends(usuario_atual),
                       db: Session = Depends(get_db)):
    cid = _id(cid)
    return _revela(db, u, req, f"cidadao:{cid}", "cidadao_id", cid, dados)


@ficha_sei.get("/gold/empresas/{empresa_id}/sei")
def sei_da_empresa(empresa_id: str, req: Request, u: Usuario = Depends(usuario_atual),
                   db: Session = Depends(get_db)):
    empresa_id = _id(empresa_id)
    return _resposta(db, u, req, f"empresa:{empresa_id}", "empresa_id", empresa_id)


@ficha_sei.post("/gold/empresas/{empresa_id}/sei/interessados")
def outros_da_empresa(empresa_id: str, dados: InteressadosIn, req: Request, u: Usuario = Depends(usuario_atual),
                      db: Session = Depends(get_db)):
    empresa_id = _id(empresa_id)
    return _lista_outros(db, u, req, f"empresa:{empresa_id}", "empresa_id", empresa_id, dados.id_procedimento)


@ficha_sei.post("/gold/empresas/{empresa_id}/sei/interessados/revelar")
def revelar_da_empresa(empresa_id: str, dados: RevelarIn, req: Request, u: Usuario = Depends(usuario_atual),
                       db: Session = Depends(get_db)):
    empresa_id = _id(empresa_id)
    return _revela(db, u, req, f"empresa:{empresa_id}", "empresa_id", empresa_id, dados)
