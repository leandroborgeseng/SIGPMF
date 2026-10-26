"""Carga completa REAL por conector (Saúde e Cemitério). Não usa a carga sintética (essa é só dev/test).

Aceite TL/Tester/Analista (CC1-CC5):
- Liberada só para admin, com a última amostra do conector sem erro e bronze > 0 (motivo visível se não).
- Leitura só pelo guard (um SELECT/WITH por vez, ROLLBACK após cada consulta, timeout da conexão), só com as
  colunas da política (allowlist; nada de CID/senha). Paginação com ORDER BY estável pela chave do mapeamento
  (OFFSET/FETCH parametrizados), lote limitado (CARGA_COMPLETA_LOTE, teto 5000) e pausa entre lotes
  (CARGA_COMPLETA_PAUSA_MS).
- Cada lote grava bronze + checkpoint numa única transação: reexecutar/retomar não duplica a bronze
  (unique (origem, tabela, id, hash)) e a silver/ouro são reconstruídas a partir da bronze (idempotentes).
- Roda em background; uma carga por conector por vez (advisory lock de sessão, o mesmo da amostra) -> 409.
- Cancelar para no próximo lote sem apagar o que já entrou; disco > RECURSOS_LIMITE_DISCO recusa o início (409) e disco
  sozinha acima de RECURSOS_LIMITE_PAUSA (retomável). Nada é apagado; o checkpoint só volta a 0 quando um ciclo completo termina.
- Card final: lidos, gravados, bronze, prata, pendências e reconciliação bronze x COUNT(*) da origem.
"""
import threading
import time
import zlib
from datetime import UTC, datetime

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.bronze.ingestao import ingere
from app.connectors.sqlserver.connector import SqlServerConnector
from app.connectors.sqlserver.politicas import politica
from app.core import recursos
from app.core.audit import audita
from app.core.config import get_settings
from app.core.db import SessionLocal
from app.gold import visao
from app.ingestion.mapeamentos import MAPEAMENTOS
from app.models.camadas import CorrelacaoPendente, RegistroBronze, Vinculo
from app.models.controle import CheckpointCarga, Execucao, Log
from app.services import carga_conector as cc
from app.services.diagnostico import linha_json
from app.silver import processamento

TIPO = "carga_completa_conector"
CONECTORES_COMPLETA = ("saude", "cemiterio")
NOMES = {"saude": "Saúde", "cemiterio": "Cemitério"}


SEM_AMOSTRA = "Rode uma amostra com sucesso primeiro"


class Bloqueado(RuntimeError):
    """Carga completa não liberada (motivo é seguro para a UI)."""


def _sistema(conector: str) -> str:
    if conector not in cc.CONECTORES:
        raise cc.ConectorDesconhecido(conector)
    return cc.CONECTORES[conector][0]


def _chave_lock(sistema: str) -> int:
    return zlib.crc32(f"amostra:{sistema}".encode())  # mesma trava da amostra: nunca amostra e completa juntas


def ordem(sistema: str, tabela: str) -> tuple[str, ...]:
    ch = MAPEAMENTOS[(sistema, tabela)]["chave"]
    return tuple(ch) if isinstance(ch, tuple) else (ch,)


def bronze_do_sistema(db: Session, sistema: str) -> int:
    return int(db.scalar(select(func.count()).select_from(RegistroBronze)
                         .where(RegistroBronze.sistema_origem == sistema)) or 0)


def motivo_bloqueio(db: Session, conector: str) -> str | None:
    """CC1: None = liberada. Senão, o motivo exibido no botão desabilitado."""
    if conector not in CONECTORES_COMPLETA:
        return "Carga completa ainda não disponível para este conector"
    sistema = _sistema(conector)
    a = db.scalar(select(Execucao).where(Execucao.tipo.in_(cc.TIPOS_AMOSTRA), Execucao.sistema == sistema)
                  .order_by(Execucao.id.desc()).limit(1))
    if a is None:
        return SEM_AMOSTRA
    if a.status not in ("sucesso", "sem_novidades") or bronze_do_sistema(db, sistema) == 0:
        return SEM_AMOSTRA
    return None


def leitor_padrao(conector: str, db: Session) -> SqlServerConnector:
    cfg, _ = cc.config_do_conector(conector, db)
    return SqlServerConnector(cfg, nome=conector)


def estimativa(db: Session, conector: str, leitor=None) -> dict:
    """CC2: COUNT(*) por tabela da política (pelo guard), para a confirmação."""
    sistema = _sistema(conector)
    pol = politica(sistema)
    leitor = leitor or leitor_padrao(conector, db)
    conn = leitor.abrir()
    try:
        tabs = {t: leitor.contar(conn, t, pol) for t in cc.tabelas_do_conector(sistema)}
    finally:
        conn.close()
    return {"tabelas": tabs, "total": sum(tabs.values())}


def ativa(db: Session, sistema: str) -> Execucao | None:
    return db.scalar(select(Execucao).where(Execucao.tipo == TIPO, Execucao.sistema == sistema,
                                            Execucao.status == "em_andamento").order_by(Execucao.id.desc()).limit(1))


def disparar(ex_id: int, conector: str, trava) -> None:
    """Background (thread daemon). Os testes substituem por execução síncrona com origem sintética."""
    threading.Thread(target=rodar, args=(ex_id, conector, trava), daemon=True, name=f"carga-completa-{conector}").start()


def iniciar(db: Session, usuario: str, conector: str) -> Execucao:
    sistema = _sistema(conector)
    motivo = motivo_bloqueio(db, conector)
    if motivo:
        raise Bloqueado(motivo)
    disco = recursos.motivo_disco_cheio()
    if disco:
        raise Bloqueado(disco)
    # Trava de sessão numa conexão dedicada que acompanha a carga até o fim (passa para o background).
    trava = db.get_bind().connect()
    if not trava.execute(text("select pg_try_advisory_lock(:k)"), {"k": _chave_lock(sistema)}).scalar():
        trava.close()
        raise cc.Conflito(f"Já existe uma carga de {NOMES[conector]} em andamento.")
    try:
        velha = ativa(db, sistema)
        if velha is not None:  # temos a trava: ninguém roda; registro ficou de um reinício do servidor
            velha.status, velha.pausa_motivo = "pausada", "Carga interrompida (reinício do servidor). Retome a carga."
            velha.finalizado_em = datetime.now(UTC)
        ex = Execucao(tipo=TIPO, sistema=sistema, status="em_andamento", usuario=usuario, cancelar=False)
        db.add(ex)
        db.commit()
    except Exception:
        _solta(trava, sistema)
        raise
    disparar(ex.id, conector, trava)
    return ex


def _solta(trava, sistema: str) -> None:
    try:
        trava.execute(text("select pg_advisory_unlock(:k)"), {"k": _chave_lock(sistema)})
        trava.commit()
    finally:
        trava.close()


def cancelar(db: Session, conector: str) -> Execucao | None:
    ex = ativa(db, _sistema(conector))
    if ex is not None:
        ex.cancelar = True
        db.commit()
    return ex


def _checkpoints(db: Session, sistema: str, tabelas: list[str]) -> dict[str, CheckpointCarga]:
    cps = {c.tabela: c for c in db.scalars(select(CheckpointCarga).where(CheckpointCarga.sistema == sistema))}
    for t in tabelas:
        if t not in cps:
            cps[t] = CheckpointCarga(sistema=sistema, tabela=t, posicao=0, concluida=False)
            db.add(cps[t])
    cps = {t: cps[t] for t in tabelas}
    if all(c.concluida for c in cps.values()):  # ciclo anterior completo: novo ciclo desde o início
        for c in cps.values():
            c.posicao, c.concluida = 0, False
    db.flush()
    return cps


def _finaliza(db: Session, ex: Execucao, status: str, mensagem: str) -> None:
    ex.status, ex.mensagem, ex.finalizado_em = status, mensagem, datetime.now(UTC)
    nivel = "ERROR" if status == "falha" else "WARN" if status in ("pausada", "cancelada") else "INFO"
    db.add(Log(execucao_id=ex.id, nivel=nivel, mensagem=mensagem))
    db.commit()
    audita(db, ex.usuario, "carga_completa_finalizada", f"execucao:{ex.id}",
           {"resultado": status, "lidas": ex.lidos, "gravadas": ex.novos + ex.atualizados, "ignoradas": ex.ignorados,
            "total_origem": ex.total_origem})


def rodar(ex_id: int, conector: str, trava, leitor=None, db: Session | None = None) -> None:
    sistema = _sistema(conector)
    proprio = db is None
    db = db or SessionLocal()
    try:
        ex = db.get(Execucao, ex_id)
        try:
            _executa(db, ex, conector, sistema, leitor or leitor_padrao(conector, db))
        except Exception as exc:  # noqa: BLE001 - lotes já confirmados ficam; o checkpoint permite retomar
            db.rollback()
            ex = db.get(Execucao, ex_id)
            ex.status = "falha"
            ex.registra_erro(exc)
            ex.tabela_atual = None
            _finaliza(db, ex, "falha", f"Falha na carga completa de {NOMES[conector]}: {ex.erro_publico()['motivo']}. "
                                       "O que já entrou foi mantido; use Tentar de novo para retomar.")
    finally:
        if proprio:
            db.close()
        if trava is not None:
            _solta(trava, sistema)


def _executa(db: Session, ex: Execucao, conector: str, sistema: str, leitor) -> None:
    s = get_settings()
    pol = politica(sistema)
    tabelas = cc.tabelas_do_conector(sistema)
    cc._garante_catalogo(db, sistema, tabelas)
    cps = _checkpoints(db, sistema, tabelas)
    db.commit()
    conn = leitor.abrir()
    try:
        for t, cp in cps.items():
            cp.total_origem = leitor.contar(conn, t, pol)
        ex.total_origem = sum(c.total_origem or 0 for c in cps.values())
        retoma = sum(c.posicao for c in cps.values())
        db.add(Log(execucao_id=ex.id, nivel="INFO", mensagem=(
            f"Carga completa de {conector}: {ex.total_origem} linhas na origem, lote={s.carga_lote}, "
            f"pausa={s.carga_pausa_ms}ms" + (f", retomando do checkpoint ({retoma} já gravadas)" if retoma else ""))))
        db.commit()
        for t, cp in cps.items():
            if cp.concluida:
                continue
            m = MAPEAMENTOS[(sistema, t)]
            cols = pol.colunas_select(t)
            ord_ = ordem(sistema, t)
            ex.tabela_atual = t
            db.commit()
            while True:
                db.refresh(ex)
                if ex.cancelar:
                    ex.tabela_atual = None
                    return _finaliza(db, ex, "cancelada", f"Carga completa de {NOMES[conector]} cancelada. "
                                     "Nada foi apagado; ao iniciar de novo, ela retoma do ponto em que parou.")
                disco = recursos.motivo_disco_cheio(recursos.limite_pausa())
                if disco:
                    ex.pausa_motivo, ex.tabela_atual = disco, None
                    return _finaliza(db, ex, "pausada", f"Carga completa pausada: {disco}")
                linhas = [linha_json(r, cols) for r in leitor.ler_pagina(conn, t, pol, ord_, cp.posicao, s.carga_lote)]
                if linhas:
                    c = ingere(db, sistema, t, m["chave"], m["campo_incremental"], linhas, ex.id, s.carga_lote,
                               rowversion=m.get("rowversion"), confirmar=False)
                    for k in ("lidos", "novos", "atualizados", "ignorados", "lotes"):
                        setattr(ex, k, (getattr(ex, k) or 0) + c[k])
                cp.posicao += len(linhas)
                cp.execucao_id = ex.id
                cp.atualizado_em = datetime.now(UTC)
                cp.concluida = len(linhas) < s.carga_lote
                db.commit()  # bronze do lote + checkpoint + progresso: tudo ou nada
                if cp.concluida:
                    break
                if s.carga_pausa_ms:
                    time.sleep(s.carga_pausa_ms / 1000)
    finally:
        conn.close()
    ex.tabela_atual = None
    sres = processamento.reconstruir(db)
    gres = visao.reconstruir(db)
    rec = reconciliacao(db, sistema)
    ok = all(r["ok"] for r in rec.values())
    db.add(Log(execucao_id=ex.id, nivel="INFO" if ok else "WARN",
               mensagem="Reconciliação bronze x origem: " + "; ".join(
                   f"{t} origem={r['origem']} bronze={r['bronze']}" for t, r in rec.items())))
    _finaliza(db, ex, "sucesso", f"Carga completa de {NOMES[conector]} concluída: lidos={ex.lidos} novos={ex.novos} "
                                 f"atualizados={ex.atualizados} ignorados={ex.ignorados} silver={sres} gold={gres} "
                                 f"reconciliacao={'ok' if ok else 'divergente'}")


def reconciliacao(db: Session, sistema: str) -> dict[str, dict]:
    """Bronze (chaves distintas por tabela) x COUNT(*) da origem lido no início da carga."""
    out = {}
    for cp in db.scalars(select(CheckpointCarga).where(CheckpointCarga.sistema == sistema).order_by(CheckpointCarga.id)):
        b = int(db.scalar(select(func.count(func.distinct(RegistroBronze.id_origem))).where(
            RegistroBronze.sistema_origem == sistema, RegistroBronze.tabela == cp.tabela)) or 0)
        out[cp.tabela] = {"origem": cp.total_origem, "bronze": b, "ok": cp.total_origem is not None and b >= cp.total_origem}
    return out


def situacao(db: Session, conector: str) -> dict:
    """Estado para a UI: liberação, última carga completa (progresso ou card final) e contagens das camadas."""
    sistema = _sistema(conector)
    ex = db.scalar(select(Execucao).where(Execucao.tipo == TIPO, Execucao.sistema == sistema)
                   .order_by(Execucao.id.desc()).limit(1))
    cps = list(db.scalars(select(CheckpointCarga).where(CheckpointCarga.sistema == sistema)))
    motivo = motivo_bloqueio(db, conector) if conector in cc.CONECTORES else None
    if motivo is None and (ex is None or ex.status != "em_andamento"):
        motivo = recursos.motivo_disco_cheio()
    execucao = None
    if ex is not None:
        execucao = {
            "id": ex.id, "status": ex.status, "usuario": ex.usuario, "iniciado_em": ex.iniciado_em,
            "finalizado_em": ex.finalizado_em, "lidos": ex.lidos, "novos": ex.novos, "atualizados": ex.atualizados,
            "ignorados": ex.ignorados, "lotes": ex.lotes, "gravados": ex.novos + ex.atualizados,
            "total_origem": ex.total_origem, "processados": sum(c.posicao for c in cps), "tabela_atual": ex.tabela_atual,
            "pausa_motivo": ex.pausa_motivo, "cancelamento_pedido": bool(ex.cancelar), "mensagem": ex.mensagem,
            "erro": ex.erro_publico()}
    return {
        "conector": conector, "liberada": motivo is None, "motivo": motivo, "execucao": execucao,
        "camadas": {
            "bronze": bronze_do_sistema(db, sistema),
            "prata": int(db.scalar(select(func.count()).select_from(Vinculo).where(Vinculo.sistema == sistema)) or 0),
            "pendencias": int(db.scalar(select(func.count()).select_from(CorrelacaoPendente).where(
                CorrelacaoPendente.sistema == sistema, CorrelacaoPendente.status == "pendente")) or 0)},
        "reconciliacao": reconciliacao(db, sistema) if ex is not None and ex.status == "sucesso" else None,
    }
