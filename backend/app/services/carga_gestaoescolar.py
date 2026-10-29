"""Carga do conector de educação (GE3/GE8/GE11): amostra primeiro, depois carga completa com hash por linha.

- Amostra: TOP (SIGPMF_GESTAOESCOLAR_AMOSTRA, máx. 50) de cada fonte; grava na bronze. Sem amostra concluída com
  sucesso, a carga completa é recusada.
- Completa: só dentro da janela noturna (AGENDA_JANELA, fuso AGENDA_FUSO), salvo SIGPMF_EDUCACAO_JANELA_NOTURNA=false
  (padrão true); com a trava desligada, a execução registra no log que a janela foi ignorada. Keyset pela PK de cada fonte com
  checkpoint (controle.checkpoints_carga) e teto de lotes por execução (SIGPMF_GESTAOESCOLAR_MAX_LOTES, padrão 1):
  atingido o teto, a execução termina como `parcial` e a próxima continua do checkpoint, sem lacuna nem duplicata.
- Hash por linha e por tabela (bronze.ingere): documento, certidão e endereço têm hash próprio, porque mudar um
  documento não muda `pes_dataAlteracao`.
- Ciclo completo (todas as fontes concluídas): reconstrói a prata da educação e zera os checkpoints.
- Nada é apagado: a bronze é append-only (versões por hash) e a prata é upsert.
- Mensagens e logs só com contagens e nomes de tabela; nunca credencial, nome, CPF ou id de pessoa.
"""
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.bronze.ingestao import ingere
from app.connectors.gestaoescolar.consultas import FONTES, ConfigGE, Fonte
from app.core.config import get_settings
from app.models.controle import CheckpointCarga, Execucao, Log
from app.services import agenda
from app.services.diagnostico import linha_json
from app.silver import educacao

TIPO_AMOSTRA = "amostra_gestaoescolar"
TIPO_COMPLETA = "carga_gestaoescolar"
SISTEMA = "GESTAOESCOLAR"
SEM_AMOSTRA = "Rode a amostra do conector de educação com sucesso antes da carga completa."
EM_ANDAMENTO = "Já existe uma carga completa do conector de educação em andamento."
NADA_EM_ANDAMENTO = "Nenhuma carga completa do conector de educação em andamento."
BATIDA_RECENTE = "A execução teve atividade nos últimos {min} min; aguarde ou cancele."
FORA_JANELA = "Carga completa do conector de educação só roda na janela noturna ({janela})."


class CargaRecusada(RuntimeError):
    pass


def _chave(f: Fonte, r: dict) -> list:
    return [None if r.get(k) is None else (r[k] if isinstance(r[k], int) else str(r[k])) for k in f.chave]


def _nova_execucao(db: Session, tipo: str, usuario: str | None, modo: str) -> Execucao:
    ex = Execucao(tipo=tipo, sistema=SISTEMA, status="em_andamento", usuario=usuario, modo=modo,
                  atualizado_em=datetime.now(UTC))
    db.add(ex)
    db.commit()
    return ex


def _ingere(db: Session, f: Fonte, linhas: list[dict], ex: Execucao) -> dict:
    linhas = [linha_json(r) for r in linhas]  # UUID, datas e Decimal do pymssql -> JSON
    return ingere(db, f.sistema, f.nome, f.chave if len(f.chave) > 1 else f.chave[0], f.campo_data, linhas, ex.id,
                  lote=max(1, len(linhas)), confirmar=False)


def _soma(ex: Execucao, c: dict) -> None:
    for k in ("lidos", "novos", "atualizados", "ignorados", "lotes"):
        setattr(ex, k, (getattr(ex, k) or 0) + c.get(k, 0))


def amostra_ok(db: Session) -> bool:
    ult = db.scalar(select(Execucao).where(Execucao.tipo == TIPO_AMOSTRA).order_by(Execucao.id.desc()).limit(1))
    return bool(ult and ult.status == "sucesso" and (ult.lidos or 0) > 0)


def dentro_da_janela(agora: datetime) -> tuple[bool, str]:
    s = get_settings()
    ini, fim = agenda.janela(s.agenda_janela)
    local = agora.astimezone(agenda._fuso())
    return agenda.na_janela(local.hour * 60 + local.minute, ini, fim), s.agenda_janela


def janela_exigida() -> bool:
    """SIGPMF_EDUCACAO_JANELA_NOTURNA (padrão true). Só afeta a carga completa da educação; o SEI não usa isto."""
    valor = get_settings().ge_janela_noturna
    return not (isinstance(valor, str) and valor.strip().lower() == "false")


def checa_janela(agora: datetime) -> bool:
    """Recusa fora da janela quando a trava está ligada. Devolve True se a checagem foi ignorada (trava desligada)."""
    if not janela_exigida():
        return True
    ok, jan = dentro_da_janela(agora)
    if not ok:
        raise CargaRecusada(FORA_JANELA.format(janela=jan))
    return False


def completa_em_andamento(db: Session) -> bool:
    return db.scalar(select(Execucao.id).where(Execucao.tipo == TIPO_COMPLETA, Execucao.status == "em_andamento")
                     .limit(1)) is not None


def solicita_cancelamento(db: Session) -> Execucao:
    """Marca o pedido (coluna `cancelar`, 0008); a carga para no fim do lote atual. Nada é apagado."""
    ex = db.scalar(select(Execucao).where(Execucao.tipo == TIPO_COMPLETA, Execucao.status == "em_andamento")
                   .order_by(Execucao.id.desc()).limit(1))
    if ex is None:
        raise CargaRecusada(NADA_EM_ANDAMENTO)
    ex.cancelar = True
    db.commit()
    return ex


def marca_falha_orfa(db: Session, eid: int, agora: datetime | None = None) -> bool:
    """UPDATE atômico: só vira `falha` se ainda está em_andamento e sem batida (atualizado_em) há mais de N min."""
    agora = agora or datetime.now(UTC)
    limite = agora - timedelta(minutes=int(get_settings().ge_orfa_min))
    r = db.execute(update(Execucao).where(
        Execucao.id == eid, Execucao.tipo == TIPO_COMPLETA, Execucao.status == "em_andamento",
        (Execucao.atualizado_em.is_(None)) | (Execucao.atualizado_em < limite),
    ).values(status="falha", finalizado_em=agora,
             mensagem=f"Marcada como falha pelo ADMIN: sem atividade há mais de {get_settings().ge_orfa_min} min."))
    db.commit()
    return r.rowcount == 1


def amostra(db: Session, leitor, cfg: ConfigGE, usuario: str | None = None) -> Execucao:
    ex = _nova_execucao(db, TIPO_AMOSTRA, usuario, "amostra")
    try:
        for f in FONTES:
            linhas = leitor.ler(f, None, min(int(cfg.amostra), 50))
            _soma(ex, _ingere(db, f, linhas, ex))
        ex.status, ex.mensagem = "sucesso", f"Amostra: {ex.lidos} linhas lidas em {len(FONTES)} tabelas."
    except Exception as exc:  # noqa: BLE001 - só a classe do erro (sem host/usuário)
        db.rollback()
        ex = db.get(Execucao, ex.id)
        ex.status, ex.mensagem = "falha", f"Amostra falhou ({type(exc).__name__})."
    ex.finalizado_em = datetime.now(UTC)
    db.commit()
    return ex


def _checkpoint(db: Session, f: Fonte) -> CheckpointCarga:
    ck = db.scalar(select(CheckpointCarga).where(CheckpointCarga.sistema == f.sistema, CheckpointCarga.tabela == f.nome))
    if ck is None:
        ck = CheckpointCarga(sistema=f.sistema, tabela=f.nome, posicao=0, ultima_chave=None, concluida=False)
        db.add(ck)
        db.flush()
    return ck


def completa(db: Session, leitor, cfg: ConfigGE, usuario: str | None = None, agora: datetime | None = None) -> Execucao:
    agora = agora or datetime.now(UTC)
    if not amostra_ok(db):
        raise CargaRecusada(SEM_AMOSTRA)
    if completa_em_andamento(db):
        raise CargaRecusada(EM_ANDAMENTO)
    janela_ignorada = checa_janela(agora)
    teto, lote = max(1, int(cfg.max_lotes)), int(cfg.lote)
    ex = _nova_execucao(db, TIPO_COMPLETA, usuario, "completa")
    if janela_ignorada:
        db.add(Log(execucao_id=ex.id, nivel="WARNING",
                   mensagem=("Janela noturna ignorada: SIGPMF_EDUCACAO_JANELA_NOTURNA=false"
                             f" (fora da janela: {'sim' if not dentro_da_janela(agora)[0] else 'não'}).")))
        db.commit()
    lotes, cancelada = 0, False
    try:
        for f in FONTES:
            if cancelada:
                break
            ck = _checkpoint(db, f)
            while not ck.concluida and lotes < teto and not cancelada:
                ultima = tuple(ck.ultima_chave) if ck.ultima_chave else None
                linhas = leitor.ler(f, ultima, lote)
                if linhas:
                    _soma(ex, _ingere(db, f, linhas, ex))
                    ck.ultima_chave = _chave(f, linha_json(linhas[-1]))
                    ck.posicao = (ck.posicao or 0) + len(linhas)
                ck.concluida = len(linhas) < lote
                ck.execucao_id, ck.atualizado_em = ex.id, datetime.now(UTC)
                ex.tabela_atual = f.nome
                lotes += 1
                db.add(Log(execucao_id=ex.id, nivel="INFO", mensagem=f"{f.sistema}.{f.nome}: lote {lotes} com {len(linhas)} linhas"))
                ex.atualizado_em = datetime.now(UTC)  # batida por lote (órfã = sem batida há N min)
                db.commit()  # lote + checkpoint na mesma transação
                db.refresh(ex)
                cancelada = bool(ex.cancelar)  # pedido de cancelamento: para no fim deste lote
        cks = [_checkpoint(db, f) for f in FONTES]
        if cancelada:
            ex.status = "cancelada"
            ex.mensagem = f"Cancelada pelo ADMIN no fim do lote {lotes}; o já gravado fica e o checkpoint continua dali."
        elif all(c.concluida for c in cks):
            r = educacao.processa(db, agora)
            for c in cks:  # ciclo completo: a próxima carga recomeça do início (pega alterações por hash)
                c.concluida, c.ultima_chave, c.posicao = False, None, 0
            ex.status = "sucesso"
            ex.mensagem = (f"Ciclo completo: {r['alunos']} alunos ({r['vinculados']} vinculados, {r['revisao']} em revisão, "
                           f"{r['quarentena']} em quarentena, {r['excluidos']} excluídos, {r['inativos']} inativos), {r['matriculas']} matrículas.")
            ex.pendentes = r["revisao"] + r["quarentena"]
        else:
            ex.status = "parcial"
            ex.mensagem = f"Parada no teto de {teto} lote(s); a próxima execução continua do checkpoint."
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        ex = db.get(Execucao, ex.id)
        ex.status, ex.mensagem = "falha", f"Carga falhou ({type(exc).__name__}); checkpoint mantido no último lote gravado."
    ex.lotes, ex.finalizado_em = lotes, datetime.now(UTC)
    db.commit()
    return ex
