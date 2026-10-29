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
from app.models.controle import Auditoria, DominioSecretaria, SecretariaRef, Usuario, UsuarioSecretaria
from app.services import acesso_secretaria as acs

ti = APIRouter(prefix="/ti/acesso-secretaria", tags=["ti"])
ACOES = {"vinculo_secretaria_adicionar": "Adicionou", "vinculo_secretaria_editar": "Editou a vigência",
         "vinculo_secretaria_remover": "Removeu", "vinculo_prefeito_remover": "Removeu (admin_lgpd)",
         "vinculo_prefeito_conceder": "Concedeu acesso de prefeito (admin_lgpd)"}
CONFLITO = "Este vínculo foi alterado por outra pessoa. Recarregue para ver a versão atual."


def _ip(req: Request) -> str | None:
    return req.client.host if req.client else None


def admin_ti(req: Request, u: Usuario = Depends(usuario_atual), db: Session = Depends(get_db)) -> Usuario:
    if u.perfil != "admin":
        audita(db, u.email, "acesso_negado", "ti:acesso-secretaria", {"perfil": u.perfil}, _ip(req))
        raise HTTPException(403, "Você não tem permissão para esta tela.")
    return u


def lgpd_prefeito(req: Request, u: Usuario = Depends(usuario_atual), db: Session = Depends(get_db)) -> Usuario:
    """TL 29/10: só o admin_lgpd remove o vínculo TODAS (prefeito), com motivo, auditado."""
    if u.perfil != "admin_lgpd":
        audita(db, u.email, "acesso_negado", "ti:prefeito-remover", {"perfil": u.perfil}, _ip(req))
        raise HTTPException(403, "Só o perfil admin_lgpd remove o acesso do prefeito.")
    return u


class SecretariaIn(BaseModel):
    codigo: str = Field(min_length=2, max_length=40, pattern=r"^[A-Z_]+$")
    nome: str = Field(min_length=3, max_length=200)
    ativo: bool = True
    a_confirmar: bool = False
    obs: str | None = Field(None, max_length=500)


class DominioIn(BaseModel):
    dominio: str = Field(min_length=2, max_length=50, pattern=r"^[a-z_]+$")
    secretaria: str = Field(min_length=2, max_length=40)
    a_confirmar: bool = False
    obs: str | None = Field(None, max_length=500)


class ConcederPrefeitoIn(BaseModel):
    usuario_id: int
    motivo: str = Field(default="", max_length=500)
    inicio: date | None = None  # sem campo de fim: o vínculo TODAS não tem data de fim


def _recusa(db: Session, u: Usuario, req: Request, acao: str, alvo_id: int, razao: str, status: int, msg: str):
    audita(db, u.email, acao, f"usuario:{alvo_id}", {"razao": razao}, _ip(req))
    raise HTTPException(status, msg)


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


def _vinculo_publico(v: UsuarioSecretaria, nomes: dict[str, str], db: Session | None = None) -> dict:
    return {"id": v.id, "secretaria": v.secretaria, "secretaria_nome": acs.nome_secretaria(v.secretaria, db),
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
def secretarias(_u: Usuario = Depends(admin_ti), db: Session = Depends(get_db)):
    """Ativas da tabela de referência (controle.secretarias), para o select da tela."""
    return [{"codigo": c, "nome": n} for c, n in acs.secretarias(db).items()]


@ti.post("/referencia/listar")
def referencia(_u: Usuario = Depends(admin_ti), db: Session = Depends(get_db)):
    """Tabela de referência completa (inclusive inativas) e o mapa domínio -> secretaria, com `a_confirmar`."""
    return {"secretarias": [{"codigo": x.codigo, "nome": x.nome, "ativo": x.ativo, "a_confirmar": x.a_confirmar, "obs": x.obs}
                            for x in db.scalars(select(SecretariaRef).order_by(SecretariaRef.codigo))],
            "dominios": [{"dominio": d.dominio, "secretaria": d.secretaria, "a_confirmar": d.a_confirmar, "obs": d.obs}
                         for d in db.scalars(select(DominioSecretaria).order_by(DominioSecretaria.dominio))]}


@ti.post("/referencia/secretaria")
def salvar_secretaria(dados: SecretariaIn, req: Request, u: Usuario = Depends(admin_ti), db: Session = Depends(get_db)):
    """TI ajusta a tabela sem deploy (upsert, nunca DELETE: desativar = ativo=false). TODAS não é desativada."""
    if dados.codigo == acs.TODAS:
        raise HTTPException(422, "A linha do prefeito não é alterada.")
    x = db.get(SecretariaRef, dados.codigo)
    antes = None if x is None else {"nome": x.nome, "ativo": x.ativo, "a_confirmar": x.a_confirmar}
    if x is None:
        x = SecretariaRef(codigo=dados.codigo)
        db.add(x)
    x.nome, x.ativo, x.a_confirmar, x.obs = dados.nome, dados.ativo, dados.a_confirmar, dados.obs
    x.atualizado_por, x.atualizado_em = u.email, datetime.now(UTC)
    audita(db, u.email, "secretaria_referencia_salvar", f"secretaria:{dados.codigo}",
           {"antes": antes, "depois": {"nome": dados.nome, "ativo": dados.ativo, "a_confirmar": dados.a_confirmar}},
           _ip(req), commit=False)
    db.commit()
    return {"codigo": x.codigo, "nome": x.nome, "ativo": x.ativo, "a_confirmar": x.a_confirmar, "obs": x.obs}


@ti.post("/referencia/dominio")
def salvar_dominio(dados: DominioIn, req: Request, u: Usuario = Depends(admin_ti), db: Session = Depends(get_db)):
    if dados.secretaria == acs.TODAS:
        raise HTTPException(422, "Um domínio não pode ficar só com o prefeito por esta rota.")
    if dados.secretaria not in acs.secretarias(db):
        raise HTTPException(422, "Secretaria desconhecida.")
    d = db.get(DominioSecretaria, dados.dominio)
    if d is not None and d.secretaria == acs.TODAS:  # QA #50 D1: domínio do prefeito não sai, não muda de dono, nada se altera
        audita(db, u.email, "dominio_secretaria_recusado", f"dominio:{dados.dominio}",
               {"razao": "dominio_do_prefeito", "destino": dados.secretaria}, _ip(req))
        raise HTTPException(422, "Este domínio é do prefeito e não é alterado por esta tela.")
    antes = None if d is None else {"secretaria": d.secretaria, "a_confirmar": d.a_confirmar}
    if d is None:
        d = DominioSecretaria(dominio=dados.dominio)
        db.add(d)
    d.secretaria, d.a_confirmar, d.obs = dados.secretaria, dados.a_confirmar, dados.obs
    d.atualizado_por, d.atualizado_em = u.email, datetime.now(UTC)
    audita(db, u.email, "dominio_secretaria_salvar", f"dominio:{dados.dominio}",
           {"antes": antes, "depois": {"secretaria": dados.secretaria, "a_confirmar": dados.a_confirmar}}, _ip(req), commit=False)
    db.commit()
    return {"dominio": d.dominio, "secretaria": d.secretaria, "a_confirmar": d.a_confirmar, "obs": d.obs}


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
            "vinculos": [_vinculo_publico(v, nomes, db) for v in vs]}


@ti.post("/vinculos/adicionar", status_code=201)
def adicionar(dados: AdicionarIn, req: Request, u: Usuario = Depends(admin_ti), db: Session = Depends(get_db)):
    alvo = _usuario(db, dados.usuario_id)
    if alvo.id == u.id:  # QA #50 c2: ninguém concede vínculo a si mesmo (qualquer secretaria)
        _recusa(db, u, req, "vinculo_secretaria_recusado", alvo.id, "auto_concessao", 403,
                "Você não pode conceder acesso a si mesmo.")
    if dados.secretaria == acs.TODAS:  # o acesso de prefeito só sai pelo fluxo próprio do admin_lgpd
        _recusa(db, u, req, "vinculo_secretaria_recusado", alvo.id, "todas_fluxo_proprio", 422,
                "O acesso de prefeito é concedido só pelo perfil admin_lgpd, em fluxo próprio.")
    if dados.secretaria not in acs.secretarias(db):  # valida contra a tabela de referência (só ativas)
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
    return _vinculo_publico(v, _nomes(db, {u.email}), db)


def _vinculo_versao(db: Session, vid: int, versao: int) -> UsuarioSecretaria:
    v = db.get(UsuarioSecretaria, vid, with_for_update=True)
    if v is None or v.removido_em is not None or v.versao != versao:
        raise HTTPException(409, CONFLITO)
    return v


@ti.post("/vinculos/editar")
def editar(dados: EditarIn, req: Request, u: Usuario = Depends(admin_ti), db: Session = Depends(get_db)):
    _valida_vigencia(dados.inicio, dados.fim)
    v = _vinculo_versao(db, dados.vinculo_id, dados.versao)
    if v.usuario_id == u.id:  # QA #50 D2: ninguém altera o próprio vínculo
        _recusa(db, u, req, "vinculo_secretaria_editar_recusado", v.usuario_id, "auto_alteracao", 403, "Você não pode alterar o seu próprio acesso.")
    if v.secretaria == acs.TODAS:  # TL 29/10: o vínculo do prefeito não é editado nem encerrado por nenhuma rota
        raise HTTPException(422, "O acesso do prefeito não é alterado por esta tela. Só o perfil admin_lgpd remove.")
    antes = {"inicio": v.vigencia_inicio.isoformat(), "fim": v.vigencia_fim.isoformat() if v.vigencia_fim else None}
    v.vigencia_inicio, v.vigencia_fim = dados.inicio, dados.fim
    v.atualizado_por, v.atualizado_em, v.versao = u.email, datetime.now(UTC), v.versao + 1
    audita(db, u.email, "vinculo_secretaria_editar", f"usuario:{v.usuario_id}",
           {"secretaria": v.secretaria, "antes": antes,
            "depois": {"inicio": dados.inicio.isoformat(), "fim": dados.fim.isoformat() if dados.fim else None}},
           _ip(req), commit=False)
    db.commit()
    db.refresh(v)
    return _vinculo_publico(v, _nomes(db, {v.cadastrado_por}), db)


@ti.post("/vinculos/remover")
def remover(dados: RemoverIn, req: Request, u: Usuario = Depends(admin_ti), db: Session = Depends(get_db)):
    motivo = dados.motivo.strip()
    if len(motivo) < acs.MOTIVO_MIN:
        raise HTTPException(422, f"Informe o motivo (mínimo de {acs.MOTIVO_MIN} caracteres).")
    v = _vinculo_versao(db, dados.vinculo_id, dados.versao)
    if v.usuario_id == u.id:  # QA #50 D2: ninguém altera o próprio vínculo
        _recusa(db, u, req, "vinculo_secretaria_remover_recusado", v.usuario_id, "auto_alteracao", 403, "Você não pode alterar o seu próprio acesso.")
    if v.secretaria == acs.TODAS:
        raise HTTPException(422, "O acesso do prefeito não é removido por esta tela. Só o perfil admin_lgpd remove.")
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
             "secretaria": acs.nome_secretaria((a.detalhe or {}).get("secretaria", ""), db),
             "motivo": (a.detalhe or {}).get("motivo")} for a in regs]


@ti.post("/vinculos/remover-prefeito")
def remover_prefeito(dados: RemoverIn, req: Request, u: Usuario = Depends(lgpd_prefeito), db: Session = Depends(get_db)):
    """TL 29/10: o vínculo TODAS (prefeito) só é removido pelo admin_lgpd, com motivo obrigatório, auditado."""
    motivo = dados.motivo.strip()
    if len(motivo) < acs.MOTIVO_MIN:
        audita(db, u.email, "vinculo_prefeito_remover_negado", None, {"razao": "motivo_invalido"}, _ip(req))
        raise HTTPException(422, f"Informe o motivo (mínimo de {acs.MOTIVO_MIN} caracteres).")
    v = _vinculo_versao(db, dados.vinculo_id, dados.versao)
    if v.usuario_id == u.id:  # QA #50 D2: ninguém altera o próprio vínculo
        _recusa(db, u, req, "vinculo_prefeito_remover_negado", v.usuario_id, "auto_alteracao", 403, "Você não pode alterar o seu próprio acesso.")
    if v.secretaria != acs.TODAS:
        raise HTTPException(422, "Esta rota remove só o acesso do prefeito.")
    v.removido_por, v.removido_em, v.motivo_remocao = u.email, datetime.now(UTC), motivo
    v.versao += 1
    audita(db, u.email, "vinculo_prefeito_remover", f"usuario:{v.usuario_id}",
           {"secretaria": v.secretaria, "motivo": motivo[:300]}, _ip(req), commit=False)
    db.commit()
    return {"id": v.id, "removido": True}


@ti.post("/vinculos/conceder-prefeito", status_code=201)
def conceder_prefeito(dados: ConcederPrefeitoIn, req: Request, u: Usuario = Depends(lgpd_prefeito),
                      db: Session = Depends(get_db)):
    """QA #50 c2 + adendo TL: só o admin_lgpd concede o TODAS, com motivo, auditado, sem data de fim. Recusa (auditada):
    a si mesmo; a quem tem perfil admin/admin_lgpd; e a quem já concedeu TODAS ao solicitante (concessão cruzada)."""
    A = "vinculo_prefeito_conceder_recusado"
    motivo = dados.motivo.strip()
    if len(motivo) < acs.MOTIVO_MIN:
        _recusa(db, u, req, A, dados.usuario_id, "motivo_invalido", 422, f"Informe o motivo (mínimo de {acs.MOTIVO_MIN} caracteres).")
    alvo = _usuario(db, dados.usuario_id)
    if alvo.id == u.id:
        _recusa(db, u, req, A, alvo.id, "auto_concessao", 403, "Você não pode conceder acesso a si mesmo.")
    if alvo.perfil in ("admin", "admin_lgpd"):
        _recusa(db, u, req, A, alvo.id, "perfil_administrativo", 422,
                "O acesso de prefeito não é concedido a contas de administração.")
    cruzada = db.scalar(select(UsuarioSecretaria.id).where(UsuarioSecretaria.usuario_id == u.id,
                                                          UsuarioSecretaria.secretaria == acs.TODAS,
                                                          UsuarioSecretaria.cadastrado_por == alvo.email).limit(1))
    if cruzada is not None:
        _recusa(db, u, req, A, alvo.id, "concessao_cruzada", 403,
                "Esta pessoa já concedeu acesso de prefeito a você. Peça a outra pessoa.")
    inicio = dados.inicio or date.today()
    v = UsuarioSecretaria(usuario_id=alvo.id, secretaria=acs.TODAS, vigencia_inicio=inicio, vigencia_fim=None,
                          cadastrado_por=u.email, versao=1)
    db.add(v)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Este usuário já tem acesso de prefeito.") from None
    audita(db, u.email, "vinculo_prefeito_conceder", f"usuario:{alvo.id}",
           {"secretaria": acs.TODAS, "motivo": motivo[:300], "inicio": inicio.isoformat()}, _ip(req), commit=False)
    db.commit()
    db.refresh(v)
    return _vinculo_publico(v, _nomes(db, {u.email}), db)
