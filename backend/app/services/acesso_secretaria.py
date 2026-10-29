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


def exige(db: Session, u: Usuario, req: Request, recurso: str, *, secretaria: str | None,
          alvo: str | None = None, menor: bool = False, revelou_cpf: bool = False) -> set[str] | None:
    """Confere o acesso antes de qualquer leitura. `secretaria=None` = qualquer vínculo vigente basta.
    Devolve as secretarias vigentes (None se a regra estiver desligada) e grava a auditoria de acesso."""
    if not rbac_ativo():
        return None
    sec = secretarias_vigentes(db, u)
    prefeito = TODAS in sec
    if not sec:
        _negar(db, u, req, recurso, alvo, "sem_vinculo_vigente")
    if not prefeito and secretaria is not None and secretaria not in sec:
        _negar(db, u, req, recurso, alvo, "outra_secretaria")
    if menor and not prefeito and SEC_MENORES not in sec:
        _negar(db, u, req, recurso, alvo, "menor_fora_educacao_saude")
    registrada = TODAS if prefeito else (secretaria or sorted(sec)[0])
    db.add(AuditoriaAcesso(usuario=u.email, secretaria=registrada, recurso=recurso, alvo=alvo, revelou_cpf=revelou_cpf,
                           ip=req.client.host if req.client else None))
    db.commit()
    return sec


def dominios_permitidos(sec: set[str] | None, dominio: str, db: Session | None = None) -> bool:
    if sec is None or TODAS in sec:
        return True
    dono = dono_dominio(db, dominio) if db is not None else None
    return dono is None or dono in sec
