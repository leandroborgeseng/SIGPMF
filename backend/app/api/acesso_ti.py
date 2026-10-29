"""PR-06 R5/R6 + UXV1–UXV8: tela de TI para vincular usuários às secretarias.

Só ADMIN (R6): qualquer outro perfil recebe 403 auditado e nenhum dado. Todas as rotas são POST: o termo de busca e o id
do usuário vão no CORPO, nunca na URL nem no log (UXV1). Toda mudança é auditada em controle.auditoria (R5), e o
histórico da tela vem dessa auditoria (só leitura). Remover é lógico (removido_em), nunca DELETE. Conflito de versão
(alguém alterou antes) = 409. Erro inesperado nunca devolve str(exc).
"""
from datetime import UTC, date, datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.audit import audita
from app.core.db import get_db
from app.core.security import usuario_atual
from app.models.controle import Auditoria, Usuario, UsuarioSecretaria
from app.services import acesso_secretaria as acs

ti = APIRouter(prefix="/ti/acesso-secretaria", tags=["ti"])
ACOES = {"vinculo_secretaria_adicionar": "Adicionou", "vinculo_secretaria_editar": "Editou a vigência",
         "vinculo_secretaria_remover": "Removeu"}
CONFLITO = "Este vínculo foi alterado por outra pessoa. Recarregue para ver a versão atual."


def _ip(req: Request) -> str | None:
    return req.client.host if req.client else None


def admin_ti(req: Request, u: Usuario = Depends(usuario_atual), db: Session = Depends(get_db)) -> Usuario:
    if u.perfil != "admin":
        audita(db, u.email, "acesso_negado", "ti:acesso-secretaria", {"perfil": u.perfil}, _ip(req))
        raise HTTPException(403, "Você não tem permissão para esta tela.")
    return u


class BuscaIn(BaseModel):
    termo: str = Field(min_length=3, max_length=200)


class UsuarioRef(BaseModel):
    usuario_id: int


class AdicionarIn(BaseModel):
    usuario_id: int
    secretaria: str = Field(min_length=1, max_length=40)
    inicio: date | None = None
    fim: date | None = None


class EditarIn(BaseModel):
    vinculo_id: int
    versao: int
    inicio: date
    fim: date | None = None


class RemoverIn(BaseModel):
    vinculo_id: int
    versao: int
    motivo: str = Field(default="", max_length=500)


def _valida_vigencia(inicio: date, fim: date | None) -> None:
    if fim is not None and fim < inicio:
        raise HTTPException(422, "A data de fim não pode ser anterior à de início.")


def _usuario(db: Session, uid: int) -> Usuario:
    alvo = db.get(Usuario, uid)
    if alvo is None:
        raise HTTPException(404, "Usuário não encontrado.")
    return alvo


def _vinculo_publico(v: UsuarioSecretaria, nomes: dict[str, str]) -> dict:
    return {"id": v.id, "secretaria": v.secretaria, "secretaria_nome": acs.nome_secretaria(v.secretaria),
            "prefeito": v.secretaria == acs.TODAS, "inicio": v.vigencia_inicio, "fim": v.vigencia_fim,
            "vencido": not acs.vigente(v) and v.vigencia_inicio <= date.today(),
            "futuro": v.vigencia_inicio > date.today(),
            "cadastrado_por": nomes.get(v.cadastrado_por, v.cadastrado_por), "cadastrado_em": v.cadastrado_em,
            "versao": v.versao}


def _nomes(db: Session, emails: set[str]) -> dict[str, str]:
    if not emails:
        return {}
    return {e: n for e, n in db.execute(select(Usuario.email, Usuario.nome).where(Usuario.email.in_(emails)))}


@ti.post("/secretarias")
def secretarias(_u: Usuario = Depends(admin_ti)):
    return [{"codigo": c, "nome": n} for c, n in acs.SECRETARIAS.items()] + [{"codigo": acs.TODAS, "nome": acs.NOME_TODAS}]


@ti.post("/usuarios/buscar")
def buscar(dados: BuscaIn, _u: Usuario = Depends(admin_ti), db: Session = Depends(get_db)):
    termo = dados.termo.strip()
    if len(termo) < 3:
        raise HTTPException(422, "Digite ao menos 3 caracteres.")
    like = f"%{termo}%"
    qtd = (select(func.count()).where(UsuarioSecretaria.usuario_id == Usuario.id, UsuarioSecretaria.removido_em.is_(None))
           .scalar_subquery())
    rows = db.execute(select(Usuario, qtd).where(or_(Usuario.nome.ilike(like), Usuario.email.ilike(like)))
                      .order_by(Usuario.nome).limit(20)).all()
    return [{"id": u.id, "nome": u.nome, "email": u.email, "perfil": u.perfil, "ativo": u.ativo, "secretarias": n}
            for u, n in rows]


@ti.post("/vinculos/listar")
def listar(dados: UsuarioRef, _u: Usuario = Depends(admin_ti), db: Session = Depends(get_db)):
    alvo = _usuario(db, dados.usuario_id)
    vs = list(db.scalars(select(UsuarioSecretaria).where(UsuarioSecretaria.usuario_id == alvo.id,
                                                         UsuarioSecretaria.removido_em.is_(None))
                         .order_by(UsuarioSecretaria.secretaria)))
    nomes = _nomes(db, {v.cadastrado_por for v in vs})
    return {"usuario": {"id": alvo.id, "nome": alvo.nome, "email": alvo.email, "perfil": alvo.perfil},
            "vinculos": [_vinculo_publico(v, nomes) for v in vs]}


@ti.post("/vinculos/adicionar", status_code=201)
def adicionar(dados: AdicionarIn, req: Request, u: Usuario = Depends(admin_ti), db: Session = Depends(get_db)):
    alvo = _usuario(db, dados.usuario_id)
    if dados.secretaria != acs.TODAS and dados.secretaria not in acs.SECRETARIAS:
        raise HTTPException(422, "Secretaria desconhecida.")
    inicio = dados.inicio or date.today()
    _valida_vigencia(inicio, dados.fim)
    v = UsuarioSecretaria(usuario_id=alvo.id, secretaria=dados.secretaria, vigencia_inicio=inicio, vigencia_fim=dados.fim,
                          cadastrado_por=u.email, versao=1)
    db.add(v)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Este usuário já tem acesso a essa secretaria. Recarregue para ver a versão atual.") from None
    audita(db, u.email, "vinculo_secretaria_adicionar", f"usuario:{alvo.id}",
           {"secretaria": v.secretaria, "inicio": inicio.isoformat(), "fim": dados.fim.isoformat() if dados.fim else None},
           _ip(req), commit=False)
    db.commit()
    db.refresh(v)
    return _vinculo_publico(v, _nomes(db, {u.email}))


def _vinculo_versao(db: Session, vid: int, versao: int) -> UsuarioSecretaria:
    v = db.get(UsuarioSecretaria, vid, with_for_update=True)
    if v is None or v.removido_em is not None or v.versao != versao:
        raise HTTPException(409, CONFLITO)
    return v


@ti.post("/vinculos/editar")
def editar(dados: EditarIn, req: Request, u: Usuario = Depends(admin_ti), db: Session = Depends(get_db)):
    _valida_vigencia(dados.inicio, dados.fim)
    v = _vinculo_versao(db, dados.vinculo_id, dados.versao)
    antes = {"inicio": v.vigencia_inicio.isoformat(), "fim": v.vigencia_fim.isoformat() if v.vigencia_fim else None}
    v.vigencia_inicio, v.vigencia_fim = dados.inicio, dados.fim
    v.atualizado_por, v.atualizado_em, v.versao = u.email, datetime.now(UTC), v.versao + 1
    audita(db, u.email, "vinculo_secretaria_editar", f"usuario:{v.usuario_id}",
           {"secretaria": v.secretaria, "antes": antes,
            "depois": {"inicio": dados.inicio.isoformat(), "fim": dados.fim.isoformat() if dados.fim else None}},
           _ip(req), commit=False)
    db.commit()
    db.refresh(v)
    return _vinculo_publico(v, _nomes(db, {v.cadastrado_por}))


@ti.post("/vinculos/remover")
def remover(dados: RemoverIn, req: Request, u: Usuario = Depends(admin_ti), db: Session = Depends(get_db)):
    motivo = dados.motivo.strip()
    if len(motivo) < acs.MOTIVO_MIN:
        raise HTTPException(422, f"Informe o motivo (mínimo de {acs.MOTIVO_MIN} caracteres).")
    v = _vinculo_versao(db, dados.vinculo_id, dados.versao)
    if v.secretaria == acs.TODAS:
        raise HTTPException(422, "O acesso do prefeito não é removido por esta tela.")
    v.removido_por, v.removido_em, v.motivo_remocao = u.email, datetime.now(UTC), motivo
    v.versao += 1
    audita(db, u.email, "vinculo_secretaria_remover", f"usuario:{v.usuario_id}",
           {"secretaria": v.secretaria, "motivo": motivo[:300]}, _ip(req), commit=False)
    db.commit()
    return {"id": v.id, "removido": True}


@ti.post("/vinculos/historico")
def historico(dados: UsuarioRef, _u: Usuario = Depends(admin_ti), db: Session = Depends(get_db)):
    alvo = _usuario(db, dados.usuario_id)
    regs = list(db.scalars(select(Auditoria).where(Auditoria.alvo == f"usuario:{alvo.id}", Auditoria.acao.in_(list(ACOES)))
                           .order_by(Auditoria.id.desc()).limit(100)))
    nomes = _nomes(db, {a.usuario for a in regs if a.usuario})
    return [{"quando": a.criado_em, "quem": nomes.get(a.usuario or "", a.usuario), "acao": ACOES[a.acao],
             "secretaria": acs.nome_secretaria((a.detalhe or {}).get("secretaria", "")),
             "motivo": (a.detalhe or {}).get("motivo")} for a in regs]
