"""Painel de progresso por fonte (PP1–PP11): só leitura de controle.execucoes, checkpoints, agendamentos e heartbeat.

- Nunca toca a origem (SQL Server, API SIL ou SEI): o total vem do que já foi gravado (checkpoints.total_origem ou a
  última carga concluída) e a tela diz de onde veio (PP3). Sem total conhecido, não há percentual (PP2).
- Só números agregados e reason codes traduzidos (PP7/PP9): nunca `mensagem` livre, `str(exc)`, segredo ou dado pessoal.
- Leve (PP8): poucas consultas pequenas por fonte, todas por índice/PK ou limitadas.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.controle import Agendamento, CheckpointCarga, Execucao, Sistema, TabelaCatalogo
from app.services.tipo_execucao import texto_limite, tipo_texto

FUSO = ZoneInfo("America/Sao_Paulo")
MIN_LOTES_PREVISAO = 3  # PP5: antes do 3º lote, "calculando…"

# PP1: status exibidos (texto sempre presente; a tela nunca depende só da cor)
RODANDO, PAUSADA, AGUARDANDO, CONCLUIDA = "rodando", "pausada", "aguardando", "concluida"
FALHOU, CANCELADA, NUNCA, NAO_LIBERADA = "falhou", "cancelada", "nunca_rodou", "nao_liberada"
STATUS_TEXTO = {RODANDO: "Rodando", PAUSADA: "Pausada", AGUARDANDO: "Aguardando", CONCLUIDA: "Concluída",
                FALHOU: "Falhou", CANCELADA: "Cancelada", NUNCA: "Nunca rodou", NAO_LIBERADA: "Não liberada"}
# UXP: Rodando, depois Falhou/Travada, Pausada/Aguardando, Concluída/Nunca rodou
ORDEM = {RODANDO: 0, FALHOU: 1, NAO_LIBERADA: 2, PAUSADA: 3, AGUARDANDO: 4, CANCELADA: 5, CONCLUIDA: 6, NUNCA: 7}

# PP7: reason code -> texto fixo em português (nunca texto livre da execução)
MOTIVOS = {
    "agenda_desligada": "Agenda desligada",
    "fora_janela": "Fora da janela noturna",
    "sei_token": "Token do SEI não configurado",
    "sei_reclassificacao": "Reclassificação incremental pendente",
    "sem_amostra": "Falta a amostra aprovada: clique em \"Rodar amostra\" no card da fonte, em Bases de origem",
    "falha_origem": "Falha ao ler a origem",
    "falha": "A carga falhou; o checkpoint ficou no último lote gravado",
    "cancelada": "Cancelada por um administrador; o que já foi gravado fica",
    "interrompida": "Carga interrompida: o servidor parou durante a carga",
    "pausada_disco": "Pausada por falta de espaço em disco",
    "pausada_sistema": "Pausada pelo sistema; continua do checkpoint",
    "parcial": "Parada no teto de lotes; a próxima execução continua do checkpoint",
}
ORIGEM_TOTAL = {"contagem": "contagem da última carga", "ultima_carga": "estimado pela última carga"}


@dataclass(frozen=True)
class Fonte:
    id: str
    nome: str
    sistemas: tuple[str, ...]          # sistemas das execuções
    sistemas_checkpoint: tuple[str, ...]
    tipos: tuple[str, ...] | None      # None = qualquer tipo do sistema
    agenda: str | None


def fontes() -> list[Fonte]:
    from app.connectors.sei.carga import SISTEMA as SEI
    from app.services import carga_completa as ccomp
    from app.services import carga_conector as cc
    from app.services import carga_gestaoescolar as cge
    out = [Fonte("educacao", "Educação", ("GESTAOESCOLAR",), ("GESTAOESCOLAR", "CORESSO"),
                 (cge.TIPO_AMOSTRA, cge.TIPO_COMPLETA), "educacao")]
    for cid, (sis, _b) in cc.CONECTORES.items():
        out.append(Fonte(cid, "SIL" if cid == "sil" else ccomp.NOMES.get(cid, sis), (sis,), (sis,), None, cid))
    out.append(Fonte("sei", "SEI", (SEI,), (SEI,), None, None))
    return out


def _utc(d: datetime | None) -> datetime | None:
    return d.replace(tzinfo=UTC) if d is not None and d.tzinfo is None else d


def _hhmm(d: datetime | None) -> str | None:
    return d.astimezone(FUSO).strftime("%H:%M") if d else None


def _ultima(db: Session, f: Fonte, *, status: tuple[str, ...] | None = None) -> Execucao | None:
    q = select(Execucao).where(Execucao.sistema.in_(f.sistemas))
    if f.tipos:
        q = q.where(Execucao.tipo.in_(f.tipos))
    if status:
        q = q.where(Execucao.status.in_(status))
    return db.scalar(q.order_by(Execucao.id.desc()).limit(1))


def _tabelas(db: Session, f: Fonte) -> list[dict]:
    """PP4: todas as tabelas da fonte (inclusive com 0 linhas), com o que o checkpoint já gravou."""
    cps = {(c.sistema, c.tabela): c for c in db.scalars(
        select(CheckpointCarga).where(CheckpointCarga.sistema.in_(f.sistemas_checkpoint),
                                      CheckpointCarga.ambiente == "producao"))}
    nomes: list[tuple[str, str]] = []
    if f.id == "educacao":
        from app.connectors.gestaoescolar.consultas import FONTES
        nomes = [(x.sistema, x.nome) for x in FONTES]
    else:
        nomes = [(s, t) for s, t in db.execute(
            select(Sistema.codigo, TabelaCatalogo.nome).join(TabelaCatalogo, TabelaCatalogo.sistema_id == Sistema.id)
            .where(Sistema.codigo.in_(f.sistemas_checkpoint)).order_by(TabelaCatalogo.id))]
    for k in cps:
        if k not in nomes:
            nomes.append(k)
    out = []
    for s, t in nomes:
        c = cps.get((s, t))
        pos = int(c.posicao or 0) if c else 0
        estado = "concluida" if c and c.concluida else ("lendo" if c and pos > 0 else "na_fila")
        out.append({"tabela": t, "linhas_processadas": pos, "total_origem": c.total_origem if c else None,
                    "estado": estado})
    return out


def _total(db: Session, f: Fonte, tabelas: list[dict], ex: Execucao) -> tuple[int | None, str | None]:
    """PP3: total já gravado no controle; nunca consulta a origem."""
    if tabelas and all(t["total_origem"] is not None for t in tabelas):
        return sum(t["total_origem"] for t in tabelas), "contagem"
    if ex.total_origem:
        return int(ex.total_origem), "contagem"
    ant = db.scalar(select(Execucao).where(Execucao.sistema.in_(f.sistemas), Execucao.tipo == ex.tipo,
                                           Execucao.status == "sucesso", Execucao.id < ex.id, Execucao.lidos > 0)
                    .order_by(Execucao.id.desc()).limit(1))
    if ant is not None and f.id != "educacao":  # educação: ciclo em várias rodadas; uma rodada não é o total
        return int(ant.lidos), "ultima_carga"
    return None, None


def _lote_tamanho(f: Fonte) -> int:
    s = get_settings()
    return max(1, int(s.ge_lote if f.id == "educacao" else s.carga_lote))


def progresso(db: Session, f: Fonte, ex: Execucao, tabelas: list[dict], agora: datetime) -> dict:
    """PP2/PP3/PP5/PP6 para uma execução em andamento."""
    tam = _lote_tamanho(f)
    processadas = sum(t["linhas_processadas"] for t in tabelas) if any(t["linhas_processadas"] for t in tabelas) else int(ex.lidos or 0)
    lote = max(int(ex.lotes or 0), math.ceil(processadas / tam)) if f.id != "educacao" else math.ceil(processadas / tam)
    total, origem = _total(db, f, tabelas, ex)
    lotes_total = max(lote, math.ceil(total / tam)) if total else None
    pct = min(100, math.floor(lote * 100 / lotes_total)) if lotes_total else None
    ini = _utc(ex.iniciado_em) or agora
    decorrido = max(0, int((agora - ini).total_seconds()))
    lotes_exec = int(ex.lotes or 0)
    previsao, calculando = None, lotes_exec < MIN_LOTES_PREVISAO
    if not calculando and lotes_total:
        media = decorrido / lotes_exec
        previsao = agora.timestamp() + media * max(0, lotes_total - lote)
    sinal = _utc(ex.atualizado_em) or ini
    sem_sinal_min = max(0, int((agora - sinal).total_seconds() // 60))
    limite = int(get_settings().ge_orfa_min)
    return {"execucao_id": ex.id, "modo": tipo_texto(ex),
            "lote": lote, "lotes_total": lotes_total, "percentual": pct, "total_origem": total,
            "origem_total": ORIGEM_TOTAL.get(origem) if origem else None, "linhas_processadas": processadas,
            "iniciado_em": ini, "decorrido_s": decorrido, "calculando": calculando,
            "previsao_termino": datetime.fromtimestamp(previsao, UTC) if previsao else None,
            "previsao_termino_brt": _hhmm(datetime.fromtimestamp(previsao, UTC)) if previsao else None,
            "ultimo_sinal": sinal, "sem_sinal_min": sem_sinal_min, "orfa_limite_min": limite,
            "possivel_travada": sem_sinal_min > limite}


def _motivo_fim(ex: Execucao) -> str | None:
    if ex.status == "falha":
        return "falha_origem" if ex.erro_classe else "falha"
    if ex.status == "cancelada":
        return "cancelada"
    if ex.status == "interrompida":
        return "interrompida"
    if ex.status == "pausada":
        return "pausada_disco" if (ex.pausa_motivo or "").lower().find("disco") >= 0 else "pausada_sistema"
    if ex.status == "parcial":
        return "parcial"
    return None


def _alerta(ex: Execucao) -> dict | None:
    """Aviso público da execução (#42), só código, nomes de tabela e texto fixo; tolera main sem o #42."""
    fn = getattr(ex, "alerta_publico", None)
    if ex.status not in ("sucesso", "sem_novidades", "parcial") or fn is None:
        return None
    a = fn()
    return {"codigo": a["codigo"], "tabelas": list(a["tabelas"]), "texto": a["texto"]} if a else None


def _sei() -> list[str] | None:
    """PP10: o que falta para liberar a carga do SEI (nomes de pendência, nunca valores)."""
    from app.connectors.sei.config import ConfigSei
    c = ConfigSei.do_ambiente()
    if c.carga_liberada and c.habilitado:
        return None
    falta = []
    if not c.habilitado:
        falta.append("sei_token")
    if not c.carga_liberada:
        falta.append("sei_reclassificacao")
    return falta


def fonte(db: Session, f: Fonte, agora: datetime) -> dict:
    ag = db.scalar(select(Agendamento).where(Agendamento.conector == f.agenda)) if f.agenda else None
    tabelas = _tabelas(db, f)
    rodando = _ultima(db, f, status=("em_andamento",))
    ult = _ultima(db, f)
    motivo, falta, aguardando_em, prog = None, None, None, None
    if rodando is not None:
        status, prog = RODANDO, progresso(db, f, rodando, tabelas, agora)
    elif f.id == "sei" and (falta := _sei()):
        status, motivo = NAO_LIBERADA, None
    elif ult is not None and ult.status in ("falha",):
        status, motivo = FALHOU, _motivo_fim(ult)
    elif ult is not None and ult.status == "cancelada":
        status, motivo = CANCELADA, "cancelada"
    elif ult is not None and ult.status in ("pausada", "interrompida"):
        status, motivo = PAUSADA, _motivo_fim(ult)
    elif ag is not None and (not ag.ativo or not get_settings().agenda_habilitada):
        # desligada (na linha ou no ambiente): nunca "próxima às ..." (antes um "Executar agora" pendente fazia
        # agenda.proxima() devolver `agora` e a tela mostrava a hora atual como próxima)
        status = PAUSADA
        motivo = "agenda_desligada"
    elif ag is not None:
        from app.services import agenda as agm
        ini, fim = agm.janela(get_settings().agenda_janela)
        status, aguardando_em = AGUARDANDO, agm.proxima(ag, agora, ini, fim)
    elif ult is not None:
        status, motivo = CONCLUIDA, _motivo_fim(ult)
    else:
        status = NUNCA
    ultima = None
    if ult is not None and rodando is None:
        ultima = {"execucao_id": ult.id, "finalizado_em": _utc(ult.finalizado_em), "lidas": int(ult.lidos or 0),
                  "gravadas": int(ult.novos or 0) + int(ult.atualizados or 0), "lotes": int(ult.lotes or 0),
                  "modo": tipo_texto(ult), "texto_limite": texto_limite(ult),  # CT15/UXP9: tipo real e teto
                  # #42: aviso de sucesso com tabela vazia ({codigo, tabelas, texto}); None sem aviso ou sem o #42
                  "alerta": _alerta(ult)}
    texto_status = STATUS_TEXTO[status]
    if status == CONCLUIDA and ultima and ultima["alerta"]:
        texto_status = "Concluída com aviso"
    return {"id": f.id, "nome": f.nome, "status": status, "status_texto": texto_status,
            "motivo_codigo": motivo, "motivo": MOTIVOS.get(motivo) if motivo else None,
            "falta": [MOTIVOS[x] for x in falta] if falta else None,
            "aguardando_em": aguardando_em, "aguardando_em_brt": _hhmm(aguardando_em),
            "progresso": prog, "ultima_execucao": ultima, "tabelas": tabelas}


def painel(db: Session, agora: datetime | None = None) -> dict:
    agora = agora or datetime.now(UTC)
    itens = [fonte(db, f, agora) for f in fontes()]
    itens.sort(key=lambda x: (ORDEM[x["status"]], x["nome"]))
    return {"agora": agora, "rodando": any(i["status"] == RODANDO for i in itens), "atualizar_a_cada_s": 30,
            "fontes": itens}
