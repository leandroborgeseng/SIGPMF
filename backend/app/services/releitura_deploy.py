"""Releitura de UMA fonte da educação no boot (SIGPMF_RELEITURA_FONTE), uso único por (fonte, SHA do deploy).

Fluxo (thread em segundo plano, só DEPOIS do /health da própria app responder 200; nunca segura o boot):
1. fonte fora do catálogo da educação -> recusa `fonte_desconhecida` (não lê nada);
2. sem SHA do deploy -> `sem_sha_deploy`; marcador `concluida` para (fonte, SHA) -> pula (INFO, nada muda);
3. RD1 (caso 348): agenda da educação com ativo=false -> `fonte_desligada`: nenhuma releitura e nenhuma execução
   criada (só auditoria `educacao_releitura_deploy_pulada` e log INFO). Com a fonte ligada, a concorrência com a
   agenda é resolvida pela trava do passo 4 (antes era recusa `agenda_ligada`); sem amostra com sucesso -> `sem_amostra`; fora da janela -> `fora_janela`;
4. pega a MESMA trava da carga (advisory lock do sistema, carga_gestaoescolar._trava) -> ocupada = `trava_ocupada`;
   já com a trava, relê o marcador: `concluida` por outra instância no mesmo SHA -> pula (`ja_executada`);
   carga completa em_andamento -> `em_andamento`;
5. segurando a trava: prepara_releitura (auditoria `educacao_releitura_fonte`, usuário `sistema-deploy`) e lê só essa
   fonte; ao fechar o ciclo a carga reprocessa a prata da educação e o vínculo com o cidadão (ouro) — educacao.processa;
6. só `sucesso` marca o marcador como `concluida`. Falha/parcial/recusa ficam registradas e o próximo boot tenta de novo.

Recusas: código fixo, sem str(exc) e sem PII: log WARNING `[releitura_deploy:<codigo>]`, auditoria
`educacao_releitura_deploy_recusada` e uma execução `releitura_deploy` (status `recusada`) com `alerta` no painel.
"""
from __future__ import annotations

import logging
import os
import re
import threading
import time
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import audita
from app.core.config import get_settings
from app.models.controle import AcaoUnica, Agendamento, Execucao, Log

_log = logging.getLogger("sigpmf.releitura_deploy")
ACAO = "releitura_fonte_educacao"
TIPO = "releitura_deploy"
USUARIO = "sistema-deploy"
VARS_SHA = ("SOURCE_COMMIT", "GIT_SHA", "SIGPMF_BUILD_SHA")  # Coolify injeta SOURCE_COMMIT; os outros são fallback
TEXTOS = {
    "fonte_desconhecida": "Releitura no deploy recusada: fonte fora do catálogo da educação. Nada foi lido.",
    "agenda_ligada": "Releitura no deploy recusada: a agenda da educação está ligada. Desligue-a; o próximo boot tenta de novo.",
    "sem_amostra": "Releitura no deploy recusada: falta uma amostra da educação com sucesso.",
    "fora_janela": "Releitura no deploy recusada: fora da janela noturna da educação.",
    "trava_ocupada": "Releitura no deploy recusada: a trava da carga da educação está ocupada por outro processo.",
    "em_andamento": "Releitura no deploy recusada: já existe carga completa da educação em andamento.",
    "sem_credencial": "Releitura no deploy recusada: credencial do conector de educação ausente ou recusada.",
    "sem_saude": "Releitura no deploy recusada: a aplicação não ficou saudável no tempo limite.",
    "sem_sha_deploy": "Releitura no deploy recusada: SHA do deploy ausente (SOURCE_COMMIT/GIT_SHA/SIGPMF_BUILD_SHA).",
    "erro_interno": "Releitura no deploy falhou antes de ler (erro interno); nada foi marcado como concluído.",
}


def alerta(ex: Execucao | None) -> dict | None:
    if ex is None or ex.tipo != TIPO or ex.status not in ("recusada", "falha") or not ex.erro_classe:
        return None
    return {"codigo": ex.erro_classe, "texto": TEXTOS.get(ex.erro_classe, TEXTOS["erro_interno"])}


def sha_deploy() -> str | None:
    """SHA do deploy: SOURCE_COMMIT (Coolify), senão GIT_SHA, senão SIGPMF_BUILD_SHA. Só hex de 7 a 40 caracteres."""
    for v in VARS_SHA:
        t = (os.environ.get(v) or "").strip().lower()
        if re.fullmatch(r"[0-9a-f]{7,40}", t):
            return t
    return None


def _marcador(db: Session, fonte: str, token: str) -> AcaoUnica:
    m = db.scalar(select(AcaoUnica).where(AcaoUnica.acao == ACAO, AcaoUnica.fonte == fonte, AcaoUnica.token == token))
    if m is None:
        m = AcaoUnica(acao=ACAO, fonte=fonte, token=token, status="pendente", usuario=USUARIO, tentativas=0)
        db.add(m)
        db.flush()
    return m


def _marca(db: Session, m: AcaoUnica | None, status: str, codigo: str | None = None, eid: int | None = None) -> None:
    if m is None:
        return
    m.status, m.codigo, m.atualizado_em = status, codigo, datetime.now(UTC)
    if eid is not None:
        m.execucao_id = eid
    db.commit()


def _recusa(db: Session, codigo: str, fonte: str, m: AcaoUnica | None) -> dict:
    db.rollback()
    texto = TEXTOS.get(codigo, TEXTOS["erro_interno"])
    agora = datetime.now(UTC)
    ex = Execucao(tipo=TIPO, sistema="GESTAOESCOLAR", status="recusada", usuario=USUARIO, modo="releitura",
                  mensagem=texto, erro_classe=codigo, finalizado_em=agora, atualizado_em=agora)
    db.add(ex)
    db.flush()
    db.add(Log(execucao_id=ex.id, nivel="WARNING", mensagem=f"[{TIPO}:{codigo}] {texto}"))
    db.commit()
    alvo = f"fonte:{fonte}" if codigo != "fonte_desconhecida" else "fonte:desconhecida"
    audita(db, USUARIO, "educacao_releitura_deploy_recusada", alvo, {"codigo": codigo, "execucao_id": ex.id})
    if m is not None:
        m = db.merge(m)
    _marca(db, m, "recusada", codigo, ex.id)
    _log.warning("[releitura_deploy:%s] releitura no deploy recusada (execucao %s)", codigo, ex.id)
    return {"resultado": "recusada", "codigo": codigo, "execucao_id": ex.id}


def executar(db: Session, fonte: str, sha: str, leitor_factory: Callable | None = None, cfg=None,
             agora: datetime | None = None) -> dict:
    """Roda a releitura uma vez (síncrono; o boot chama em thread). Devolve resultado/código, sem PII."""
    from app.connectors.gestaoescolar.consultas import POR_NOME, ConfigGE, LeitorGE
    from app.services import carga_gestaoescolar as cge
    fonte = (fonte or "").strip()
    if fonte not in POR_NOME:
        return _recusa(db, "fonte_desconhecida", fonte, None)
    ag = db.scalar(select(Agendamento).where(Agendamento.conector == "educacao"))
    if ag is not None and not ag.ativo:  # RD1 (caso 348): fonte desligada -> nenhuma releitura, nenhuma execução
        db.rollback()
        audita(db, USUARIO, "educacao_releitura_deploy_pulada", f"fonte:{fonte}", {"codigo": "fonte_desligada"})
        _log.info("[releitura_deploy:fonte_desligada] agenda da educação com ativo=false; releitura não criada")
        return {"resultado": "pulada", "codigo": "fonte_desligada", "execucao_id": None}
    m = _marcador(db, fonte, sha)
    if m.status == "concluida":
        db.commit()
        _log.info("[releitura_deploy:ja_executada] fonte %s já relida neste SHA; nada a fazer", fonte)
        return {"resultado": "ja_executada", "codigo": "ja_executada", "execucao_id": m.execucao_id}
    m.tentativas = (m.tentativas or 0) + 1
    _marca(db, m, "em_andamento")
    if not cge.amostra_ok(db):
        return _recusa(db, "sem_amostra", fonte, m)
    agora = agora or datetime.now(UTC)
    try:
        janela_ignorada = cge.checa_janela(agora)
    except cge.CargaRecusada:
        return _recusa(db, "fora_janela", fonte, m)
    if cfg is None:
        cfg = ConfigGE.do_ambiente(get_settings())
        try:
            cfg.valida()
        except PermissionError:
            return _recusa(db, "sem_credencial", fonte, m)
    trava = cge._trava(db)  # mesma trava (advisory lock) da carga completa/amostra da educação
    if trava is None:
        return _recusa(db, "trava_ocupada", fonte, m)
    try:
        # R2: confere o marcador DE NOVO já segurando a trava. Outra instância com o mesmo SHA pode ter concluído
        # entre a 1ª conferência e a trava; sem isto a releitura rodaria duas vezes.
        db.refresh(m)
        if m.status == "concluida":
            db.commit()
            _log.info("[releitura_deploy:ja_executada] fonte %s concluída por outra instância neste SHA; nada a fazer", fonte)
            return {"resultado": "ja_executada", "codigo": "ja_executada", "execucao_id": m.execucao_id}
        if cge.completa_em_andamento(db):
            return _recusa(db, "em_andamento", fonte, m)
        try:
            cge.prepara_releitura(db, fonte, USUARIO)
        except cge.CargaRecusada as exc:
            return _recusa(db, exc.codigo if exc.codigo in TEXTOS else "erro_interno", fonte, m)
        _log.info("[releitura_deploy] fonte %s: checkpoints preparados; lendo só essa fonte", fonte)
        fabrica = leitor_factory or (lambda: LeitorGE(cfg))
        with fabrica() as leitor:
            ex = None
            for _ in range(10000):  # mesma trava o tempo todo; para em sucesso/falha/cancelada
                ex = cge._completa(db, leitor, cfg, USUARIO, agora, janela_ignorada)
                if ex.status != "parcial":
                    break
                agora = datetime.now(UTC)
    except Exception as exc:  # noqa: BLE001  (motivo fixo; nunca str(exc))
        db.rollback()
        m = db.merge(m)
        _marca(db, m, "falha", "erro_interno")
        _log.warning("[releitura_deploy:erro_interno] releitura falhou (%s); marcador não concluído", type(exc).__name__)
        return {"resultado": "falha", "codigo": "erro_interno", "execucao_id": None}
    finally:
        cge._solta(trava)
    m = db.merge(m)
    if ex is not None and ex.status == "sucesso":
        _marca(db, m, "concluida", None, ex.id)  # prata e vínculo ao cidadão (ouro) já reprocessados no fim do ciclo
        audita(db, USUARIO, "educacao_releitura_deploy", f"execucao:{ex.id}", {"fonte": fonte, "status": ex.status,
                                                                                  "lidos": ex.lidos})
        _log.info("[releitura_deploy] fonte %s relida e prata/ouro da educação reprocessados (execucao %s)", fonte, ex.id)
        return {"resultado": "concluida", "codigo": None, "execucao_id": ex.id}
    st = ex.status if ex is not None else "falha"
    _marca(db, m, "falha", ex.erro_classe if ex is not None and ex.erro_classe else st, ex.id if ex else None)
    audita(db, USUARIO, "educacao_releitura_deploy_falha", f"execucao:{ex.id if ex else '-'}", {"fonte": fonte, "status": st})
    _log.warning("[releitura_deploy:%s] releitura não concluiu (execucao %s); próximo boot tenta de novo", st,
                 ex.id if ex else None)
    return {"resultado": "falha", "codigo": st, "execucao_id": ex.id if ex else None}


def url_saude() -> str:
    """R3: /health da própria app; a porta vem de SIGPMF_RELEITURA_SAUDE_PORTA (padrão 8000)."""
    return f"http://127.0.0.1:{get_settings().releitura_saude_porta}/health"


def aguarda_saude(url: str | None = None, max_s: int | None = None, passo: float = 2.0) -> bool:
    url = url or url_saude()
    fim = time.monotonic() + (max_s or get_settings().releitura_saude_max_s)
    while time.monotonic() < fim:
        try:
            with urllib.request.urlopen(url, timeout=5) as r:  # noqa: S310 (URL local: 127.0.0.1 + porta inteira validada)
                if r.status == 200:
                    return True
        except Exception:  # noqa: BLE001
            pass
        time.sleep(passo)
    return False


def no_boot(saude: Callable[[], bool] | None = None, leitor_factory: Callable | None = None, cfg=None) -> dict | None:
    """Chamado pela thread do boot. Sem SIGPMF_RELEITURA_FONTE: nada acontece (None)."""
    from app.core.db import SessionLocal
    fonte = (get_settings().releitura_fonte or "").strip()
    if not fonte:
        return None
    with SessionLocal() as db:
        try:
            if not (saude or aguarda_saude)():
                return _recusa(db, "sem_saude", fonte, None)
            sha = sha_deploy()
            if sha is None:  # sem SHA não há como garantir uso único: recusa em vez de arriscar repetir
                return _recusa(db, "sem_sha_deploy", fonte, None)
            return executar(db, fonte, sha, leitor_factory=leitor_factory, cfg=cfg)
        except Exception as exc:  # noqa: BLE001
            _log.warning("[releitura_deploy:erro_interno] %s", type(exc).__name__)
            return {"resultado": "falha", "codigo": "erro_interno", "execucao_id": None}


def iniciar_em_fundo() -> threading.Thread | None:
    """Lifespan: dispara a thread (daemon) e volta na hora; o boot e o /health nunca esperam por ela."""
    if not (get_settings().releitura_fonte or "").strip():
        return None
    _log.info("[releitura_deploy] SIGPMF_RELEITURA_FONTE definida; aguardando /health para começar em segundo plano")
    t = threading.Thread(target=no_boot, name="releitura-deploy", daemon=True)
    t.start()
    return t
