"""Rotas do shell de painéis. Escopo do secretário checado AQUI, no backend (P08), sempre (não depende do front)."""
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.core.audit import audita
from app.core.db import get_db
from app.core.security import usuario_atual
from app.models.controle import Usuario
from app.paineis import servico
from app.services.acesso_secretaria import TODAS, secretarias_vigentes

paineis = APIRouter(prefix="/paineis", tags=["paineis"])
PERFIS_TUDO = ("admin",)  # TI administra a plataforma; prefeito = vínculo TODAS


def escopo_do_usuario(db: Session, u: Usuario) -> frozenset[str] | None:
    """None = vê todas as secretarias (prefeito/TI). Conjunto = só as secretarias com vínculo vigente."""
    if u.perfil in PERFIS_TUDO:
        return None
    sec = secretarias_vigentes(db, u)
    if TODAS in sec:
        return None
    return frozenset(sec)


@paineis.get("")
def lista(u: Usuario = Depends(usuario_atual)):
    return [{"chave": p.chave, "titulo": p.titulo, "exemplo": p.exemplo} for p in servico.PAINEIS.values()]


@paineis.get("/{chave}")
def painel(chave: str, req: Request, competencia: str | None = None, secretaria: str | None = None,
           caminho: list[str] = Query(default=[]), u: Usuario = Depends(usuario_atual), db: Session = Depends(get_db)):
    """Só agregados do ouro. Sem vínculo vigente (e sem ser prefeito/TI) = 403; outra secretaria = 403 auditado."""
    esc = escopo_do_usuario(db, u)
    if esc is not None and not esc:
        audita(db, u.email, "painel_negado", chave, {"razao": "sem_vinculo_vigente"}, req.client.host if req.client else None)
        raise HTTPException(403, "Sem vínculo vigente com uma secretaria.")
    try:
        return servico.painel(db, chave, competencia=competencia, secretaria=secretaria, caminho=caminho[:3], escopo=esc)
    except servico.AcessoNegado:
        audita(db, u.email, "painel_negado", chave, {"razao": "outra_secretaria"}, req.client.host if req.client else None)
        raise HTTPException(403, "Sem acesso a dados desta secretaria") from None
    except servico.PedidoInvalido as e:
        raise HTTPException(400, str(e)) from None
