"""PR-06: acesso por secretaria (R1–R4) e manutenção dos vínculos pela tela de TI (R5/R6).

Regra (com SIGPMF_RBAC_SECRETARIA=true):
- Domínio -> secretaria vem de controle.dominio_secretaria (TI ajusta sem deploy).
- Com vínculo vigente o dado nominal sai SEM máscara, inclusive o CPF completo (TL 29/10), e a linha de
  controle.auditoria_acesso marca revelou_cpf.
- Dado nominal de um domínio só sai para quem tem vínculo VIGENTE (não removido, início <= hoje <= fim) com a secretaria
  dona do domínio (R1). Outra secretaria, sem vínculo ou vínculo vencido = 403 sem nenhum dado nem contagem (R2).
- O vínculo "TODAS" é o do prefeito: vê todas as secretarias, com auditoria (R3).
- Menor de idade só para a Educação (e prefeito); dado clínico só para a Saúde (R4, decisão do TL 29/10) — a API nominal
  da Saúde não devolve conteúdo clínico, e os indicadores clínicos são agregados.
- Toda consulta liberada grava uma linha em controle.auditoria_acesso (R1/R3); toda recusa vai para controle.auditoria.

Com a variável desligada (padrão), nada muda no comportamento atual: a regra entra em produção só depois que a TI
cadastrar os vínculos (senão todo usuário perderia o acesso no deploy). Valor só "true" (qualquer caixa) liga.
"""
import os
from dataclasses import dataclass
from datetime import date

from fastapi import HTTPException, Request
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.audit import audita
from app.models.controle import AuditoriaAcesso, Usuario, UsuarioSecretaria

TODAS = "TODAS"
NOME_TODAS = "Todas as secretarias (prefeito)"
SEC_MENORES = "EDUCACAO"  # menores: só a Educação (TL 29/10); clínico: só a Saúde
SEC_CLINICO = "SAUDE"
MOTIVO_MIN = 10


def rbac_ativo() -> bool:
    return (os.environ.get("SIGPMF_RBAC_SECRETARIA") or "").strip().lower() == "true"


def secretarias(db: Session, so_ativas: bool = True) -> dict[str, str]:
    """Tabela de referência controle.secretarias (a TI ajusta sem deploy)."""
    from app.models.controle import SecretariaRef
    q = select(SecretariaRef)
    if so_ativas:
        q = q.where(SecretariaRef.ativo.is_(True))
    return {x.codigo: x.nome for x in db.scalars(q.order_by(SecretariaRef.nome))}


def nome_secretaria(codigo: str, db: Session | None = None) -> str:
    if db is not None:
        from app.models.controle import SecretariaRef
        r = db.get(SecretariaRef, codigo)
        if r is not None:
            return r.nome
    return NOME_TODAS if codigo == TODAS else codigo


def dono_dominio(db: Session, dominio: str) -> str | None:
    from app.models.controle import DominioSecretaria
    r = db.get(DominioSecretaria, dominio)
    return r.secretaria if r else None


def vigente(v: UsuarioSecretaria, hoje: date | None = None) -> bool:
    h = hoje or date.today()
    return v.removido_em is None and v.vigencia_inicio <= h and (v.vigencia_fim is None or v.vigencia_fim >= h)


def secretarias_vigentes(db: Session, usuario: Usuario, hoje: date | None = None) -> set[str]:
    h = hoje or date.today()
    q = select(UsuarioSecretaria.secretaria).where(
        UsuarioSecretaria.usuario_id == usuario.id, UsuarioSecretaria.removido_em.is_(None),
        UsuarioSecretaria.vigencia_inicio <= h,
        or_(UsuarioSecretaria.vigencia_fim.is_(None), UsuarioSecretaria.vigencia_fim >= h))
    return set(db.scalars(q))


def _negar(db: Session, u: Usuario, req: Request, recurso: str, alvo: str | None, razao: str) -> None:
    # Sem PII: só o recurso, o alvo técnico (id) e a razão fixa. A resposta não diz se o cidadão existe.
    audita(db, u.email, "acesso_secretaria_negado", alvo, {"recurso": recurso, "razao": razao},
           req.client.host if req.client else None)
    raise HTTPException(403, "Sem acesso a dados desta secretaria")


@dataclass(frozen=True)
class Acesso:
    """Resultado da regra. `ativo=False`: regra desligada (comportamento antigo). `dominios=None`: todos (prefeito)."""
    ativo: bool
    dominios: frozenset[str] | None = None

    def ve(self, dominio: str | None) -> bool:
        return not self.ativo or self.dominios is None or (dominio or "") in self.dominios

    @property
    def sem_mascara(self) -> bool:
        return self.ativo


DESLIGADO = Acesso(False)


def dominios_do_usuario(db: Session, sec: set[str]) -> frozenset[str] | None:
    """Domínios cuja secretaria DONA está entre as vigentes do usuário. Domínio sem dono nunca entra (só prefeito)."""
    from app.models.controle import DominioSecretaria
    if TODAS in sec:
        return None
    return frozenset(db.scalars(select(DominioSecretaria.dominio).where(DominioSecretaria.secretaria.in_(sec))))


def exige(db: Session, u: Usuario, req: Request, recurso: str, *, dominios: list[str] | set[str] | tuple[str, ...],
          alvo: str | None = None, menor: bool = False, revelou_cpf: bool = False) -> Acesso:
    """Confere o acesso ANTES de qualquer leitura devolvida. Libera só se o usuário tem vínculo vigente com a secretaria
    dona de pelo menos um dos `dominios` (prefeito: todos). Menor de idade: só a Educação (e prefeito).
    Grava auditoria_acesso na liberação e `acesso_secretaria_negado` (sem PII) na recusa."""
    if not rbac_ativo():
        return DESLIGADO
    sec = secretarias_vigentes(db, u)
    if not sec:
        _negar(db, u, req, recurso, alvo, "sem_vinculo_vigente")
    meus = dominios_do_usuario(db, sec)
    if meus is not None:
        liberados = [d for d in dominios if d in meus]
        if not liberados:
            _negar(db, u, req, recurso, alvo, "outra_secretaria")
        if menor and SEC_MENORES not in sec:
            _negar(db, u, req, recurso, alvo, "menor_so_educacao")
        registrada = dono_dominio(db, liberados[0]) or "?"
    else:
        registrada = TODAS
    db.add(AuditoriaAcesso(usuario=u.email, secretaria=registrada, recurso=recurso, alvo=alvo, revelou_cpf=revelou_cpf,
                           ip=req.client.host if req.client else None))
    db.commit()
    return Acesso(True, meus)


def exige_lista(db: Session, u: Usuario, req: Request, recurso: str) -> Acesso:
    """Listas nominais: precisa de vínculo vigente com secretaria dona de algum domínio; o filtro é feito na consulta."""
    if not rbac_ativo():
        return DESLIGADO
    sec = secretarias_vigentes(db, u)
    if not sec:
        _negar(db, u, req, recurso, None, "sem_vinculo_vigente")
    meus = dominios_do_usuario(db, sec)
    if meus is not None and not meus:
        _negar(db, u, req, recurso, None, "outra_secretaria")
    db.add(AuditoriaAcesso(usuario=u.email, secretaria=TODAS if meus is None else (dono_dominio(db, sorted(meus)[0]) or "?"),
                           recurso=recurso, ip=req.client.host if req.client else None))
    db.commit()
    return Acesso(True, meus)


def ve_menores(db: Session, u: Usuario) -> bool:
    sec = secretarias_vigentes(db, u)
    return TODAS in sec or SEC_MENORES in sec


def dom(*dominios: str):
    """Dependência FastAPI: a rota inteira exige vínculo vigente com a secretaria dona de um destes domínios."""
    from fastapi import Depends

    from app.core.db import get_db
    from app.core.security import usuario_atual

    def dep(req: Request, u: Usuario = Depends(usuario_atual), db: Session = Depends(get_db)) -> Acesso:
        return exige(db, u, req, req.url.path.split("/api/", 1)[-1][:50], dominios=dominios)
    return dep
