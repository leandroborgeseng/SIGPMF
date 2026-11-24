"""Menu Plano de Governo: usa JWT, vínculos e auditoria do SIGPMF."""
import json

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.audit import audita
from app.core.db import get_db
from app.core.security import usuario_atual
from app.models.controle import SecretariaRef, Usuario
from app.plano_governo import dominio
from app.plano_governo.modelo import PlanoGovernoEstado
from app.services.acesso_secretaria import TODAS, secretarias_vigentes

plano = APIRouter(prefix="/plano-governo", tags=["plano-governo"])
CONFLITO = "Os dados foram atualizados por outra pessoa. Recarregue o plano antes de salvar."


class AcaoIn(BaseModel):
    version: int = Field(ge=0)
    action: str = Field(min_length=1, max_length=60)
    data: dict = Field(default_factory=dict)


def ator(db: Session, usuario: Usuario) -> dict:
    sec = secretarias_vigentes(db, usuario)
    todas = usuario.perfil == "admin" or TODAS in sec
    papel = "admin" if usuario.perfil == "admin" else "prefeito" if TODAS in sec and usuario.perfil == "operador" else (
        "secretaria" if usuario.perfil == "operador" else "leitura")
    return {"id": usuario.id, "name": usuario.nome, "email": usuario.email, "role": papel,
            "departmentIds": sorted(sec - {TODAS}), "allDepartments": todas}


def carregar(db: Session):
    secretarias = [{"id": x.codigo, "name": x.nome, "leader": "", "color": "#0066cc"}
                   for x in db.scalars(select(SecretariaRef).where(
                       SecretariaRef.ativo.is_(True), SecretariaRef.codigo != TODAS).order_by(SecretariaRef.nome))]
    registro = db.get(PlanoGovernoEstado, 1)
    estado = json.loads(json.dumps(registro.conteudo)) if registro else dominio.inicial(secretarias)
    estado = dominio.atualizar_base(estado, secretarias)
    estado["departments"] = secretarias
    return registro, estado


def autorizar_leitura(db, usuario, estado, req):
    actor = ator(db, usuario)
    visivel = dominio.visivel(estado, actor)
    possui_projeto = bool(visivel["projects"])
    if not actor["allDepartments"] and not actor["departmentIds"] and not possui_projeto:
        audita(db, usuario.email, "plano_governo_negado", "plano_governo", {"razao": "sem_vinculo_vigente"},
               req.client.host if req.client else None)
        raise HTTPException(403, "Sem vínculo vigente ou projeto atribuído. Solicite o acesso à TI do SIGPMF.")
    return actor, visivel


@plano.get("/state")
def estado(req: Request, response: Response, u: Usuario = Depends(usuario_atual), db: Session = Depends(get_db)):
    registro, dados = carregar(db)
    usuario, vis = autorizar_leitura(db, u, dados, req)
    response.headers["Cache-Control"] = "no-store"
    return {"version": registro.versao if registro else 0, "state": vis, "user": usuario}


@plano.post("/action")
def acao(dados: AcaoIn, req: Request, u: Usuario = Depends(usuario_atual), db: Session = Depends(get_db)):
    if len(json.dumps(dados.data)) > 1_000_000:
        raise HTTPException(413, "Requisição muito grande.")
    registro, atual = carregar(db)
    usuario, _vis = autorizar_leitura(db, u, atual, req)
    versao = registro.versao if registro else 0
    if dados.version != versao:
        raise HTTPException(409, CONFLITO)
    try:
        novo = dominio.aplicar(atual, dados.action, dados.data, usuario)
    except dominio.SemPermissao:
        audita(db, u.email, "plano_governo_negado", "plano_governo", {"acao": dados.action},
               req.client.host if req.client else None)
        raise HTTPException(403, "Sem permissão para esta ação no Plano de Governo.") from None
    except dominio.PedidoInvalido as exc:
        raise HTTPException(422, str(exc)) from None
    try:
        if registro:
            resultado = db.execute(update(PlanoGovernoEstado).where(
                PlanoGovernoEstado.id == 1, PlanoGovernoEstado.versao == versao).values(
                    conteudo=novo, versao=versao + 1))
            if resultado.rowcount != 1:
                db.rollback()
                raise HTTPException(409, CONFLITO)
        else:
            db.add(PlanoGovernoEstado(id=1, versao=1, conteudo=novo))
            db.flush()
        evento = novo["history"][0]
        audita(db, u.email, "plano_governo_alterado", f"plano:{evento['subject']}",
               {"acao": dados.action, "versao": versao + 1}, req.client.host if req.client else None, commit=False)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, CONFLITO) from None
    return {"ok": True, "version": versao + 1}
