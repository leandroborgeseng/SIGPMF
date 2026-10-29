"""Aviso de tabela vazia: tabela que termina a carga COMPLETA com 0 linhas lidas (todas as fontes).

Não muda o status (`sucesso` continua sucesso, para não quebrar agenda, assinatura e ultima_completa_em), mas:
- grava um log WARNING por execução com as tabelas (só nomes de tabela, nunca dado de linha);
- marca a execução com o código fixo `tabela_vazia` (erro_classe) e as tabelas (erro_tabela);
- acrescenta o aviso à mensagem e expõe `alerta` na API (Execucao.alerta_publico), visível no card e na agenda.
Carga incremental com 0 linhas é normal (nada mudou) e não gera aviso.
"""
import logging

from sqlalchemy.orm import Session

from app.models.controle import Execucao, Log

CODIGO = "tabela_vazia"
_log = logging.getLogger(__name__)


def texto(tabelas: list[str]) -> str:
    return (f"Atenção: {len(tabelas)} tabela(s) terminaram a carga completa com 0 linhas lidas "
            f"({', '.join(tabelas)}). Confira filtros, escopo e permissões da origem.")


def registra(db: Session, ex: Execucao, sistema: str | None, tabelas: list[str], nivel: str = "WARNING") -> str:
    """Marca a execução e grava o WARNING. Devolve o sufixo da mensagem ('' se não há tabela vazia)."""
    tabelas = sorted(dict.fromkeys(t for t in tabelas if t))
    if not tabelas:
        return ""
    msg = texto(tabelas)
    ex.erro_classe, ex.erro_tabela = CODIGO, ", ".join(tabelas)[:200]
    db.add(Log(execucao_id=ex.id, nivel=nivel, mensagem=f"[{CODIGO}] {sistema or ''}: {msg}".replace(" : ", ": ")))
    _log.warning("execucao %s (%s): %d tabela(s) com 0 linhas na carga completa", ex.id, sistema, len(tabelas))
    return " " + msg


def alerta(ex: Execucao | None) -> dict | None:
    if ex is None or ex.erro_classe != CODIGO or not ex.erro_tabela:
        return None
    tabelas = [t.strip() for t in ex.erro_tabela.split(",") if t.strip()]
    return {"codigo": CODIGO, "tabelas": tabelas, "texto": texto(tabelas)}
