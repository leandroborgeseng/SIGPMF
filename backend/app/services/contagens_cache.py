"""Cache das contagens por camada (bronze/prata/ouro/pendências) das telas de conectores.

Em produção, os COUNT(*) sobre milhões de linhas levavam de 43 a 60 s e o nginx cortava GET /api/conectores.
A listagem passa a ler um retrato já calculado (do próprio Postgres, nunca da origem):
- retrato fresco (idade < CONECTORES_CACHE_S e mesma chave) -> devolve na hora;
- retrato velho ou chave mudou (execução nova/terminada) -> recalcula numa thread (uma por vez) e espera no máximo
  CONECTORES_CACHE_ESPERA_S; se não terminar, devolve o retrato anterior marcado como "atualizando";
- sem retrato nenhum (logo após subir) -> devolve None e a rota responde zeros com "contagens_atualizando".
Com CONECTORES_CACHE_S=0 (padrão em APP_ENV=test) recalcula sempre e espera o fim: os testes continuam exatos.
Nada aqui abre conexão com SQL Server ou SIL; só SELECT no Postgres da aplicação.
"""
import logging
import os
import threading
import time
from collections.abc import Callable
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.controle import Execucao

log = logging.getLogger(__name__)


def ttl_s() -> float:
    padrao = "0" if os.environ.get("APP_ENV") == "test" else "60"
    return float(os.environ.get("CONECTORES_CACHE_S", padrao))


def espera_s() -> float:
    return float(os.environ.get("CONECTORES_CACHE_ESPERA_S", "0.5"))


def chave_execucoes(db: Session) -> tuple:
    """Muda quando uma execução começa ou termina (barato: índice da PK + uma agregação pequena)."""
    r = db.execute(select(func.max(Execucao.id), func.max(Execucao.finalizado_em),
                          func.count().filter(Execucao.status == "em_andamento"))).one()
    return tuple(r)


class _Entrada:
    __slots__ = ("valor", "em", "chave", "thread")

    def __init__(self):
        self.valor, self.em, self.chave, self.thread = None, 0.0, None, None


_lock = threading.Lock()
_entradas: dict[str, _Entrada] = {}


def limpa() -> None:
    with _lock:
        _entradas.clear()


def _sessao() -> Session:
    from app.core.db import SessionLocal
    return SessionLocal()


def _recalcula(nome: str, ent: _Entrada, chave: tuple, calcula: Callable[[Session], Any]) -> None:
    db = _sessao()
    try:
        valor = calcula(db)
        with _lock:
            ent.valor, ent.em, ent.chave = valor, time.monotonic(), chave
    except Exception as exc:  # noqa: BLE001 - só a classe no log; a tela segue com o retrato anterior
        log.warning("contagens %s: recalculo falhou (%s)", nome, type(exc).__name__)
    finally:
        db.close()


def obter(db: Session, nome: str, calcula: Callable[[Session], Any]) -> tuple[Any, bool]:
    """(valor, atualizando). valor None = ainda não há retrato."""
    chave = chave_execucoes(db)
    ttl = ttl_s()
    with _lock:
        ent = _entradas.setdefault(nome, _Entrada())
        fresco = ent.valor is not None and ent.chave == chave and (time.monotonic() - ent.em) < ttl
        if fresco:
            return ent.valor, False
        th = ent.thread
        if th is None or not th.is_alive():
            th = threading.Thread(target=_recalcula, args=(nome, ent, chave, calcula), daemon=True,
                                  name=f"contagens-{nome}")
            ent.thread = th
            th.start()
    th.join(None if ttl <= 0 else espera_s())
    with _lock:
        return ent.valor, th.is_alive()


def aquecer_em_fundo() -> threading.Thread:
    """Boot: calcula o retrato da listagem numa thread, para a 1ª abertura da tela já achar as contagens."""
    def _roda():
        from app.services.indicadores import contagens_por_sistema
        db = _sessao()
        try:
            obter(db, "conectores", contagens_por_sistema)
        except Exception as exc:  # noqa: BLE001
            log.warning("aquecer contagens: %s", type(exc).__name__)
        finally:
            db.close()
    t = threading.Thread(target=_roda, daemon=True, name="contagens-aquecer")
    t.start()
    return t
