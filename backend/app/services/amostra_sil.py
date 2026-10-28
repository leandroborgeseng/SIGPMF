"""Disparo da amostra SIL pela API (decisão TL 27/10, SIL9): só admin, auditado, UM lote por execução.

Mesmo modelo da carga completa (e da amostra do #22): a rota valida e pega a trava, cria a execução, responde 202
e a consulta roda numa thread com sessão própria. Autocontido (sem depender do #22):
- trava de sessão `crc32("amostra:SIL")` numa conexão dedicada: uma amostra SIL por vez -> 409 (Conflito);
- a thread SEMPRE solta a trava e nunca deixa a execução em_andamento;
- só o endpoint paginado (consultarlote) pode ser disparado aqui: os endpoints por chave dependem das chaves do
  bronze de IPTU/ISS (SIL4), ainda em escopo com o Analista;
- auditoria: quem pediu, a execução e o resultado (contagens). Nenhum documento, URL, token ou segredo."""
import threading
import zlib
from datetime import UTC, datetime

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.connectors.sil import carga
from app.connectors.sil.cliente import SilDesabilitado
from app.connectors.sil.config import ConfigSil
from app.connectors.sil.endpoints import endpoint
from app.models.controle import Execucao, Log

CHAVE_TRAVA = zlib.crc32(b"amostra:SIL")
ENDPOINT_AMOSTRA = "empresas_lote"
LOTES_POR_EXECUCAO = 1  # TL: um lote por execução, independentemente de SIL_CONSULTARLOTE_MAX_LOTES
STATUS_SUCESSO = ("sucesso", "sem_novidades")  # demais (falha, interrompida, origem vazia...) -> etapa "falha"


class Conflito(RuntimeError):
    pass


class NaoDisponivel(RuntimeError):
    pass


def em_andamento(db: Session) -> Execucao | None:
    return db.scalar(select(Execucao).where(Execucao.tipo == carga.TIPO_EXECUCAO, Execucao.sistema == carga.SISTEMA,
                                            Execucao.status == "em_andamento").order_by(Execucao.id.desc()).limit(1))


def _solta(trava) -> None:
    try:
        trava.execute(text("select pg_advisory_unlock(:k)"), {"k": CHAVE_TRAVA})
        trava.commit()
    finally:
        trava.close()


def iniciar_amostra(db: Session, usuario: str, nome_endpoint: str = ENDPOINT_AMOSTRA) -> Execucao:
    if nome_endpoint != ENDPOINT_AMOSTRA:
        raise NaoDisponivel("Só a listagem de empresas (consultarlote) pode ser disparada nesta versão; "
                            "as consultas por chave dependem das chaves do IPTU/ISS.")
    if not ConfigSil.do_ambiente().habilitado:
        raise SilDesabilitado("Conector SIL desligado: defina SIL_BASE_URL, SIL_CLIENT_ID e SIL_CLIENT_SECRET.")
    trava = db.get_bind().connect()
    if not trava.execute(text("select pg_try_advisory_lock(:k)"), {"k": CHAVE_TRAVA}).scalar():
        trava.close()
        ex = em_andamento(db)
        raise Conflito("Já existe uma amostra SIL em andamento" + (f" (execução {ex.id})." if ex else "."))
    try:
        # temos a trava: execução em_andamento que sobrou (reinício) não está viva
        for velha in list(db.scalars(select(Execucao).where(Execucao.tipo == carga.TIPO_EXECUCAO,
                                                             Execucao.status == "em_andamento"))):
            velha.status, velha.finalizado_em = "interrompida", datetime.now(UTC)
            velha.mensagem = "Amostra SIL interrompida: sem processo vivo."
            db.add(Log(execucao_id=velha.id, nivel="WARN", mensagem=velha.mensagem))
        ex = Execucao(tipo=carga.TIPO_EXECUCAO, sistema=carga.SISTEMA, status="em_andamento", usuario=usuario,
                      etapa="leitura", atualizado_em=datetime.now(UTC))
        db.add(ex)
        db.commit()
    except Exception:
        _solta(trava)
        raise
    disparar(ex.id, usuario, nome_endpoint, trava)
    return ex


def disparar(ex_id: int, usuario: str, nome_endpoint: str, trava) -> None:
    """Background (thread daemon). Os testes trocam por execução síncrona."""
    threading.Thread(target=rodar, args=(ex_id, usuario, nome_endpoint, trava), daemon=True,
                     name=f"amostra-sil-{ex_id}").start()


def rodar(ex_id: int, usuario: str, nome_endpoint: str, trava, cliente=None) -> None:
    from app.core.audit import audita
    from app.core.db import SessionLocal
    db = SessionLocal()
    try:
        ex = carga.executar_enriquecimento(db, usuario, endpoint(nome_endpoint), cliente=cliente,
                                           execucao_id=ex_id, max_lotes=LOTES_POR_EXECUCAO)
        ex.etapa = "concluida" if ex.status in STATUS_SUCESSO else "falha"
        db.commit()
        audita(db, usuario, "amostra_sil_executada", f"execucao:{ex_id}",
               {"status": ex.status, "lidos": ex.lidos, "novos": ex.novos, "lotes": ex.lotes})
    except Exception as exc:  # noqa: BLE001 - rede de segurança
        db.rollback()
        ex = db.get(Execucao, ex_id)
        if ex is not None and ex.status == "em_andamento":
            ex.status, ex.finalizado_em, ex.etapa = "falha", datetime.now(UTC), "falha"
            ex.mensagem = f"Falha na amostra SIL: {type(exc).__name__}"
            db.add(Log(execucao_id=ex.id, nivel="ERROR", mensagem=ex.mensagem))
            db.commit()
            audita(db, usuario, "amostra_sil_executada", f"execucao:{ex_id}", {"status": "falha"})
    finally:
        db.close()
        _solta(trava)
