"""API do explorador (EX1–EX7). Todas as rotas são POST: filtros, tabela e ids vão no CORPO, nunca na URL (EX6).
Acesso: admin e admin_lgpd (o admin_lgpd precisa ver para revelar). Qualquer outro perfil → 403 auditado.
Revelar: só admin_lgpd, com motivo, auditado; a tela volta a mascarar após REVELAR_CPF_SEGUNDOS."""
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.audit import audita
from app.core.config import get_settings
from app.core.db import get_db
from app.core.security import usuario_atual
from app.explorador import servico as sv
from app.models.controle import Usuario

PERFIS_EXPLORADOR = ("admin", "admin_lgpd")
PERFIL_REVELA = "admin_lgpd"  # decisão do TL: admin comum vê tudo mascarado

from app.services.acesso_secretaria import dom_ou_lgpd as _dom_pr06  # noqa: E402

# PR-06: com SIGPMF_RBAC_SECRETARIA=true, o explorador (dado bruto de todas as áreas) exige o dono de "explorador" (prefeito)
# ou admin_lgpd por função com motivo no cabeçalho X-SIGPMF-Motivo.
explorador = APIRouter(prefix="/explorar", tags=["explorar"], dependencies=[Depends(_dom_pr06("explorador"))])


def _ip(req: Request) -> str | None:
    return req.client.host if req.client else None


def acesso_explorador(req: Request, u: Usuario = Depends(usuario_atual), db: Session = Depends(get_db)) -> Usuario:
    if u.perfil not in PERFIS_EXPLORADOR:
        audita(db, u.email, "explorar_acesso_negado", req.url.path, {"perfil": u.perfil}, _ip(req))
        raise HTTPException(403, "Perfil sem permissão para explorar dados")
    return u


Camada = Literal["bronze", "prata", "ouro"]


class ConectorIn(BaseModel):
    conector: str = Field(..., max_length=50)


class CamadaIn(ConectorIn):
    camada: Camada


class TabelaIn(CamadaIn):
    tabela: str = Field(..., max_length=200)


class AmostraIn(TabelaIn):
    pagina: int = Field(1, ge=1, le=100000)
    filtros: dict[str, Any] = Field(default_factory=dict, max_length=10)
    somente_pendencias: bool = False
    motivo_filtro: str | None = Field(None, max_length=500)


class RevelarIn(TabelaIn):
    linha_id: str | int
    coluna: str = Field(..., max_length=64)
    motivo: str | None = None


class LinhagemIn(BaseModel):
    cidadao_id: str = Field(..., max_length=36)


def _aud(db, u, acao, req, **det):
    """Auditoria sem valores de filtro (só nomes de coluna): nada identificável no registro."""
    audita(db, u.email, f"explorar_{acao}", "explorador", det, _ip(req))


@explorador.post("/conectores")
def conectores(req: Request, u: Usuario = Depends(acesso_explorador), db: Session = Depends(get_db)):
    out = [{"conector": c, "estado": sv.estado(db, c)} for c in sv.conectores(db)]
    _aud(db, u, "conectores", req)
    return {"conectores": out, "pode_revelar": u.perfil == PERFIL_REVELA}


@explorador.post("/tabelas")
def tabelas(d: CamadaIn, req: Request, u: Usuario = Depends(acesso_explorador), db: Session = Depends(get_db)):
    c = sv.valida_conector(db, d.conector)
    r = sv.tabelas(db, c, d.camada)
    _aud(db, u, "tabelas", req, conector=c, camada=d.camada)
    return r


@explorador.post("/perfil")
def perfil(d: TabelaIn, req: Request, u: Usuario = Depends(acesso_explorador), db: Session = Depends(get_db)):
    c = sv.valida_conector(db, d.conector)
    r = sv.perfil(db, c, d.camada, d.tabela)
    _aud(db, u, "perfil", req, conector=c, camada=d.camada, tabela=d.tabela)
    return r


@explorador.post("/amostra")
def amostra(d: AmostraIn, req: Request, u: Usuario = Depends(acesso_explorador), db: Session = Depends(get_db)):
    c = sv.valida_conector(db, d.conector)
    spec_ = sv.cat.POR_CAMADA.get(d.camada, {}).get(d.tabela) if d.camada != "bronze" else None
    pend = d.somente_pendencias and d.camada == "prata"  # pendências só existem na prata; na bronze a flag não libera nada
    sens = [] if pend else sv.colunas_sensiveis_filtradas(d.filtros, spec_, bronze=d.camada == "bronze")
    if sens:  # filtro em coluna pessoal (ou qualquer filtro além de id na bronze): só admin_lgpd, com motivo, auditado
        alvo = f"{d.camada}:{d.tabela}:{','.join(sens)}"
        if u.perfil != PERFIL_REVELA:
            audita(db, u.email, "explorar_filtro_pessoal_negado", alvo, {"colunas": sens}, _ip(req))
            raise HTTPException(403, "Filtro na bronze (além de id) ou em coluna pessoal disponível só para o perfil LGPD"
                                if d.camada == "bronze" else "Filtro em coluna pessoal disponível só para o perfil LGPD")
        motivo = (d.motivo_filtro or "").strip()
        if len(motivo) < get_settings().revelar_motivo_min:
            raise HTTPException(400, f"Informe o motivo do filtro (mínimo {get_settings().revelar_motivo_min} caracteres).")
        audita(db, u.email, "explorar_filtro_pessoal", alvo,
               {"conector": c, "tabela": d.tabela, "colunas": sens, "motivo": motivo}, _ip(req), commit=False)
    r = sv.amostra(db, c, d.camada, d.tabela, d.pagina, d.filtros, d.somente_pendencias)
    _aud(db, u, "amostra", req, conector=c, camada=d.camada, tabela=d.tabela, pagina=d.pagina,
         filtro_colunas=sorted(k for k, v in d.filtros.items() if v not in (None, "")),
         somente_pendencias=d.somente_pendencias)
    return r


@explorador.post("/funil")
def funil(d: ConectorIn, req: Request, u: Usuario = Depends(acesso_explorador), db: Session = Depends(get_db)):
    c = sv.valida_conector(db, d.conector)
    r = sv.funil(db, c)
    _aud(db, u, "funil", req, conector=c)
    return r


@explorador.post("/linhagem")
def linhagem(d: LinhagemIn, req: Request, u: Usuario = Depends(acesso_explorador), db: Session = Depends(get_db)):
    r = sv.linhagem(db, d.cidadao_id)
    _aud(db, u, "linhagem", req, cidadao=d.cidadao_id)
    return r


@explorador.post("/revelar")
def revelar(d: RevelarIn, req: Request, u: Usuario = Depends(acesso_explorador), db: Session = Depends(get_db)):
    alvo = f"{d.camada}:{d.tabela}:{d.linha_id}:{d.coluna}"
    if u.perfil != PERFIL_REVELA:
        audita(db, u.email, "explorar_revelar_negado", alvo, ip=_ip(req))
        raise HTTPException(403, "Só o perfil admin_lgpd revela dados pessoais")
    motivo = (d.motivo or "").strip()
    if len(motivo) < get_settings().revelar_motivo_min:
        raise HTTPException(400, f"Informe o motivo (mínimo {get_settings().revelar_motivo_min} caracteres).")
    c = sv.valida_conector(db, d.conector)
    v = sv.valor_original(db, c, d.camada, d.tabela, d.linha_id, d.coluna)
    audita(db, u.email, "explorar_revelar", alvo, {"conector": c, "motivo": motivo}, _ip(req))
    return {"valor": v, "remascarar_em_segundos": get_settings().revelar_cpf_segundos}
