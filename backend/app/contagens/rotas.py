"""Rotas do J1–J4. Executar: só admin. Agregados: admin e admin_lgpd. CPFs repetidos (J4): só admin_lgpd, auditado."""
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.contagens import job
from app.contagens.modelos import ContagemCpfRepetido, ContagemExecucao, ContagemResultado
from app.core.audit import audita
from app.core.db import get_db
from app.core.security import ADMIN, REVELA_CPF, exige_perfil, usuario_atual
from app.models.controle import Usuario

contagens = APIRouter(dependencies=[Depends(usuario_atual)])
LEITURA = exige_perfil("admin", "admin_lgpd")


def _ip(req: Request) -> str | None:
    return req.client.host if req.client else None


def _execucao(db: Session, ex: ContagemExecucao) -> dict:
    res = db.scalars(select(ContagemResultado).where(ContagemResultado.execucao_id == ex.id)
                     .order_by(ContagemResultado.id))
    return {"id": ex.id, "status": ex.status, "codigo": ex.codigo, "limiar_cpf": ex.limiar_cpf,
            "iniciado_em": ex.iniciado_em, "terminado_em": ex.terminado_em,
            "resultados": [{"consulta": r.consulta, "base": r.base, "status": r.status, "codigo": r.codigo,
                            "sql_hash": r.sql_hash, "resultado": r.resultado} for r in res]}


def _ultima_id(db: Session) -> int | None:
    return db.scalar(select(ContagemExecucao.id).order_by(ContagemExecucao.id.desc()).limit(1))


@contagens.post("/contagens/executar")
def executar(req: Request, u: Usuario = Depends(ADMIN), db: Session = Depends(get_db)):
    ex = job.executar(db, u.email)
    return _execucao(db, ex)


@contagens.get("/contagens/ultima")
def ultima(u: Usuario = Depends(LEITURA), db: Session = Depends(get_db)):
    ex = db.scalar(select(ContagemExecucao).order_by(ContagemExecucao.id.desc()).limit(1))
    if ex is None:
        raise HTTPException(404, "Nenhuma contagem executada")
    return _execucao(db, ex)


@contagens.get("/contagens/{eid}/cpfs-repetidos")
def cpfs_repetidos(eid: int, req: Request, u: Usuario = Depends(REVELA_CPF), db: Session = Depends(get_db)):
    ex = db.get(ContagemExecucao, eid)
    if ex is None:
        raise HTTPException(404, "Contagem não encontrada")
    from datetime import UTC, datetime
    job.expurga_cpfs(db, _ultima_id(db), datetime.now(UTC))  # J4b também na leitura (nada com mais de 30 dias)
    linhas = list(db.scalars(select(ContagemCpfRepetido).where(ContagemCpfRepetido.execucao_id == eid,
                                                               ContagemCpfRepetido.cpf.is_not(None))
                             .order_by(ContagemCpfRepetido.ocorrencias.desc())))
    audita(db, u.email, "contagens_cpf_repetido_leitura", f"contagem:{eid}", {"linhas": len(linhas)}, _ip(req))
    return {"execucao_id": eid, "limiar": ex.limiar_cpf,
            "itens": [{"base": r.base, "tabela": r.tabela, "cpf": r.cpf, "ocorrencias": r.ocorrencias} for r in linhas]}
