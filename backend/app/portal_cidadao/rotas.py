import secrets
from datetime import date, timedelta
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field, ValidationError, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import audita
from app.core.config import get_settings
from app.core.db import get_db
from app.core.security import ADMIN
from app.identity.cpf import mascara, normaliza, valido
from app.models.camadas import Cidadao, DadoGold, Identificador, Imovel, ImovelPossuidor, Lancamento
from app.models.controle import Usuario
from app.portal_cidadao import servico as s
from app.portal_cidadao.modelos import AcessoCidadao

portal = APIRouter(prefix="/portal", tags=["portal-cidadao"])


class Identificar(BaseModel):
    cpf: str = Field(max_length=18)
    nascimento: date
    nome_mae: str = Field(min_length=5, max_length=200)

    @field_validator("cpf")
    @classmethod
    def valida_cpf(cls, v):
        if not valido(v):
            raise ValueError("Informe um CPF válido.")
        return normaliza(v)

    @field_validator("nascimento")
    @classmethod
    def valida_nascimento(cls, v):
        if v > date.today() or v.year < 1900:
            raise ValueError("Confira sua data de nascimento.")
        return v


class Confirmar(BaseModel):
    desafio: str = Field(min_length=32, max_length=100)
    codigo: str = Field(pattern=r"^\d{6}$")


class Guia(BaseModel):
    tipo: str = Field(pattern=r"^(lancamento|acordo)$")
    id: str = Field(min_length=1, max_length=200)


class AcordoOut(BaseModel):
    id: str = Field(min_length=1, max_length=200)
    descricao: str = Field(max_length=300)
    situacao: str = Field(max_length=100)
    valor: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    vencimento: date | None = None


class AcordosOut(BaseModel):
    acordos: list[AcordoOut] = Field(max_length=300)


def pronto():
    cfg = get_settings()
    return bool(cfg.portal_smtp_host and cfg.portal_smtp_from)


def protecao(req, token):
    recebido = req.headers.get("X-Portal-CSRF", "")
    if not secrets.compare_digest(recebido, s.resumo_hash("csrf", token)):
        raise HTTPException(403, "Atualize a página antes de continuar.")


@portal.get("/config")
def config(response: Response):
    response.headers["Cache-Control"] = "no-store"
    return {"identificacao_disponivel": pronto(), "emissao_disponivel": bool(get_settings().portal_tributos_url)}


@portal.post("/identificar")
def identificar(d: Identificar, req: Request, tarefas: BackgroundTasks, response: Response, db: Session = Depends(get_db)):
    response.headers["Cache-Control"] = "no-store"
    s.limitar(db, req, d.cpf)
    if not pronto():
        raise HTTPException(503, "O acesso digital está em preparação. A confirmação de identidade ainda não foi ativada pela Prefeitura.")
    cid, email = s.conferir(db, d.cpf, d.nascimento, d.nome_mae)
    desafio, codigo = secrets.token_urlsafe(32), f"{secrets.randbelow(1000000):06d}"
    db.add(AcessoCidadao(chave=s.resumo_hash("desafio", desafio), tipo="desafio", cidadao_id=cid,
                         segredo_hash=s.resumo_hash(desafio, codigo), tentativas=0, expira_em=s.agora() + timedelta(minutes=10)))
    audita(db, "portal", "portal_identificacao", "portal", {}, req.client.host if req.client else None, commit=False)
    db.commit()
    if cid and email:
        tarefas.add_task(s.enviar_codigo, email, codigo)
    return {"desafio": desafio, "mensagem": s.GENERICA, "expira_segundos": 600}


@portal.post("/acesso-teste")
def acesso_teste(d: Identificar, req: Request, response: Response,
                 administrador: Usuario = Depends(ADMIN), db: Session = Depends(get_db)):
    """Acesso de homologação autorizado pelo admin já autenticado, sem dispensar cadastro conferido."""
    s.limitar(db, req, d.cpf)
    cid, _ = s.conferir(db, d.cpf, d.nascimento, d.nome_mae, exigir_contato=False)
    if not cid:
        raise HTTPException(422, "Os dados não conferem com o cadastro municipal. Confira CPF, nascimento e nome completo da mãe.")
    token = s.criar_sessao(db, cid, teste=True)
    audita(db, administrador.email, "portal_acesso_teste", f"cidadao:{cid}",
           {"autorizacao": "administrador_sigpmf", "sem_codigo": True},
           req.client.host if req.client else None, commit=False)
    db.commit()
    response.set_cookie(s.COOKIE, token, max_age=900, httponly=True, secure=get_settings().app_env == "prod",
                        samesite="strict", path="/api/portal")
    response.headers["Cache-Control"] = "no-store"
    return {"autenticado": True, "modo_teste": True, "csrf": s.resumo_hash("csrf", token)}


@portal.post("/confirmar")
def confirmar(d: Confirmar, req: Request, response: Response, db: Session = Depends(get_db)):
    s.limitar(db, req)
    r = db.scalar(select(AcessoCidadao).where(AcessoCidadao.chave == s.resumo_hash("desafio", d.desafio)).with_for_update())
    if not r or r.tipo != "desafio" or s.utc(r.expira_em) <= s.agora() or r.tentativas >= 5:
        raise HTTPException(401, "Código inválido ou expirado. Solicite um novo acesso.")
    r.tentativas += 1
    valido_codigo = secrets.compare_digest(r.segredo_hash or "", s.resumo_hash(d.desafio, d.codigo))
    if not valido_codigo or not r.cidadao_id:
        db.commit()
        raise HTTPException(401, "Código inválido ou expirado. Solicite um novo acesso.")
    cid = r.cidadao_id
    db.delete(r)  # Uso único, bloqueado na transação.
    token = s.criar_sessao(db, cid)
    audita(db, "portal", "portal_acesso", f"cidadao:{cid}", {}, req.client.host if req.client else None, commit=False)
    db.commit()
    response.set_cookie(s.COOKIE, token, max_age=900, httponly=True, secure=get_settings().app_env == "prod", samesite="strict", path="/api/portal")
    response.headers["Cache-Control"] = "no-store"
    return {"autenticado": True, "csrf": s.resumo_hash("csrf", token)}


@portal.post("/sair")
def sair(req: Request, response: Response, db: Session = Depends(get_db)):
    r = s.sessao(db, req)
    protecao(req, req.cookies[s.COOKIE])
    db.delete(r)
    db.commit()
    response.delete_cookie(s.COOKIE, path="/api/portal")
    return {"ok": True}


@portal.get("/me")
def meus_dados(req: Request, response: Response, db: Session = Depends(get_db)):
    r = s.sessao(db, req)
    c = db.get(Cidadao, r.cidadao_id)
    if not c or c.falecido:
        raise HTTPException(401, "Não foi possível abrir o cadastro. Procure o atendimento municipal.")
    cpf = db.scalar(select(Identificador.valor).where(Identificador.cidadao_id == c.cidadao_id, Identificador.tipo == "cpf"))
    dados = list(db.scalars(select(DadoGold).where(DadoGold.cidadao_id == c.cidadao_id,
                         DadoGold.mais_recente.is_(True), DadoGold.campo.in_(["endereco", "email", "telefone"])) ))
    posses = list(db.scalars(select(ImovelPossuidor).where(ImovelPossuidor.cidadao_id == c.cidadao_id, ImovelPossuidor.ativo.is_(True))))
    imoveis = list(db.scalars(select(Imovel).where(Imovel.codi.in_([p.codi for p in posses])))) if posses else []
    lancs = list(db.scalars(select(Lancamento).where(Lancamento.cidadao_id == c.cidadao_id)
                           .order_by(Lancamento.vencimento.desc().nulls_last()).limit(300)))
    hoje = date.today()
    f = lambda v: None if v is None else float(v)  # noqa: E731
    abertos = [x for x in lancs if not x.pago]
    vencidos = [x for x in abertos if x.vencimento and x.vencimento < hoje]
    audita(db, "portal", "portal_consulta_propria", f"cidadao:{c.cidadao_id}", {}, req.client.host if req.client else None)
    response.headers["Cache-Control"] = "no-store"
    return {"modo_teste": r.tipo == "sessao_teste", "nome": c.nome, "cpf": mascara(cpf), "nascimento": c.data_nascimento, "atualizado_em": c.atualizado_em,
            "cadastro": [{"campo": d.campo, "valor": d.valor, "fonte": d.sistema, "atualizado_em": d.fonte_atualizado_em} for d in dados],
            "imoveis": [{"id": i.codi, "inscricao": i.inscricao, "descricao": i.complemento or "Imóvel cadastrado"} for i in imoveis],
            "lancamentos": [{"id": str(x.id), "tributo": x.tributo or "Tributo municipal", "exercicio": x.exercicio,
                "parcela": x.parcela, "imovel": x.codi, "vencimento": x.vencimento, "valor": f(x.valor),
                "situacao": "pago" if x.pago else "vencido" if x.vencimento and x.vencimento < hoje else "a_vencer"} for x in lancs],
            "totais": {"em_aberto": sum(f(x.valor) or 0 for x in abertos), "vencido": sum(f(x.valor) or 0 for x in vencidos)},
            "lista_limitada": len(lancs) == 300, "csrf": s.resumo_hash("csrf", req.cookies[s.COOKIE]), "expira_em": r.expira_em,
            "aviso_valores": "Valores da última base importada. Juros, descontos e situação atual devem ser confirmados na emissão da guia oficial."}


def integracao(db, cid, operacao, extra=None):
    cfg = get_settings()
    url = cfg.portal_tributos_url
    if not url or urlparse(url).scheme != "https" or not cfg.portal_tributos_token:
        raise HTTPException(503, "A emissão de guias e a consulta de acordos aguardam integração com o sistema tributário municipal.")
    cpf = db.scalar(select(Identificador.valor).where(Identificador.cidadao_id == cid, Identificador.tipo == "cpf"))
    try:
        with httpx.Client(timeout=20, follow_redirects=False) as cliente:
            res = cliente.post(url.rstrip("/") + "/" + operacao,
                headers={"Authorization": "Bearer " + cfg.portal_tributos_token.get_secret_value()}, json={"cpf": cpf, **(extra or {})})
            res.raise_for_status()
            return res.json()
    except (httpx.HTTPError, ValueError):
        raise HTTPException(502, "O sistema tributário não respondeu. Tente novamente mais tarde.") from None


@portal.get("/acordos")
def acordos(req: Request, response: Response, db: Session = Depends(get_db)):
    r = s.sessao(db, req)
    resposta = integracao(db, r.cidadao_id, "acordos")
    response.headers["Cache-Control"] = "no-store"
    if not isinstance(resposta, dict) or not isinstance(resposta.get("acordos"), list):
        raise HTTPException(502, "A consulta de acordos está temporariamente indisponível.")
    try:
        return AcordosOut.model_validate(resposta)
    except ValidationError:
        raise HTTPException(502, "A consulta de acordos está temporariamente indisponível.") from None


@portal.post("/guias")
def guias(d: Guia, req: Request, response: Response, db: Session = Depends(get_db)):
    r = s.sessao(db, req)
    protecao(req, req.cookies[s.COOKIE])
    s.limitar(db, req)
    if d.tipo == "lancamento":
        if not d.id.isdigit():
            raise HTTPException(404, "Lançamento não encontrado.")
        lanc = db.get(Lancamento, int(d.id))
        if not lanc or lanc.cidadao_id != r.cidadao_id or lanc.pago:
            raise HTTPException(404, "Lançamento não encontrado ou já quitado.")
        identificador = lanc.chave_origem
    else:
        lista = integracao(db, r.cidadao_id, "acordos")
        if not any(str(a.get("id")) == d.id for a in lista.get("acordos", [])):
            raise HTTPException(404, "Acordo não encontrado.")
        identificador = d.id
    guia = integracao(db, r.cidadao_id, "guias", {"tipo": d.tipo, "id": identificador})
    destino = guia.get("url", "") if isinstance(guia, dict) else ""
    u = urlparse(destino)
    permitidos = {x.strip().lower() for x in get_settings().portal_guias_hosts.split(",") if x.strip()}
    if u.scheme != "https" or u.hostname not in permitidos or u.username or u.password:
        raise HTTPException(502, "A guia não pôde ser validada. Procure o atendimento municipal.")
    audita(db, "portal", "portal_emissao_guia", f"cidadao:{r.cidadao_id}", {"tipo": d.tipo}, req.client.host if req.client else None)
    response.headers["Cache-Control"] = "no-store"
    return {"url": destino, "mensagem": "Confira os valores, vencimento e beneficiário no documento oficial. A emissão não confirma pagamento."}
