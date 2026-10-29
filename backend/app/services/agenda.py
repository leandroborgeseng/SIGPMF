"""Agenda das cargas completas (AG1–AG6).

- AG1: um horário diário por conector (`controle.agendamentos`), ligado/desligado pelo admin. A agenda inteira só roda
  com AGENDA_HABILITADA=true (desligada por padrão) e nunca em APP_ENV=test.
- AG2: UMA carga por vez, na ordem da agenda, e só dentro de AGENDA_JANELA ("00:00-06:00"; "00:00-24:00" = o dia
  todo, para a homologação). A carga é a mesma da tela (`carga_completa.iniciar`): mesma trava, checkpoint, pausa
  entre lotes, limite de disco e auditoria; o agendador espera ela terminar antes de passar para a próxima.
- AG3: antes de ler, compara a assinatura da origem (COUNT(*) e MAX(campo incremental) por tabela, pelo guard) com a
  da última carga bem-sucedida. Igual -> "sem_mudanca": nenhuma linha é lida e prata/ouro não são reconstruídas.
- AG4: falha -> nova tentativa depois de AGENDA_REESPERA_MIN, até AGENDA_TENTATIVAS; depois fica para o dia seguinte.
  Falha avisa por e-mail quando SMTP_HOST e ALERTA_PARA estão configurados (sem host, usuário, senha nem CPF).
- AG5: só um processo agenda (advisory lock de sessão `agenda:lider`), mesmo com vários workers.
- AG6: "Executar agora" (admin) marca `forcar`; o próximo ciclo roda o conector mesmo fora do horário e da janela.
- IN3: no dia a dia a carga é INCREMENTAL (só o alterado desde a marca d'água); a cada AGENDA_COMPLETA_DIAS (e na
  primeira vez) é COMPLETA, para pegar exclusões na origem. Em ambas, prata/ouro só são refeitas se a bronze mudou.
"""
import logging
import re
import smtplib
import threading
import time
import zlib
from datetime import UTC, date, datetime, timedelta
from email.message import EmailMessage
from zoneinfo import ZoneInfo

from sqlalchemy import select, text, update
from sqlalchemy.orm import Session

from app.connectors.sqlserver.politicas import politica
from app.core.audit import audita
from app.core.config import get_settings
from app.core.db import SessionLocal, get_engine
from app.ingestion.mapeamentos import MAPEAMENTOS
from app.models.controle import Agendamento, Execucao
from app.services import carga_completa as ccomp
from app.services import carga_conector as cc

log = logging.getLogger("sigpmf.agenda")

USUARIO = "agenda"
CHAVE_LIDER = zlib.crc32(b"agenda:lider")
HORARIO = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
JANELA = re.compile(r"^(\d{2}):(\d{2})-(\d{2}):(\d{2})$")
RESOLVIDOS = ("sucesso", "sem_mudanca")
STATUS_TEXTO = {"sucesso": "Concluída", "sem_mudanca": "Sem mudança na origem",
                "parcial": "Parcial (teto de lotes; há mais na origem)", "falha": "Falhou",
                "bloqueada": "Não liberada", "ocupada": "Conector ocupado", "pausada": "Pausada",
                "cancelada": "Cancelada", "interrompida": "Interrompida", "demorada": "Ainda em andamento"}


MAX_MENSAGEM = 300  # ultima_mensagem na tela (QA #37 R1)


class AgendaInvalida(ValueError):
    """Valor recusado (mensagem segura para a tela)."""


# ---------------------------------------------------------------- janela e horários

def janela(texto: str) -> tuple[int, int]:
    """"HH:MM-HH:MM" -> (início, fim) em minutos do dia; fim 24:00 = 1440. Início > fim atravessa a meia-noite."""
    m = JANELA.match((texto or "").strip())
    if not m:
        raise AgendaInvalida("AGENDA_JANELA deve ser HH:MM-HH:MM (ex.: 00:00-06:00 ou 00:00-24:00).")
    ini, fim = int(m[1]) * 60 + int(m[2]), int(m[3]) * 60 + int(m[4])
    if not (0 <= ini < 1440 and 0 < fim <= 1440) or int(m[2]) > 59 or int(m[4]) > 59:
        raise AgendaInvalida("AGENDA_JANELA fora do intervalo 00:00-24:00.")
    return ini, fim


def dia_todo(ini: int, fim: int) -> bool:
    return ini == 0 and fim == 1440


def na_janela(minuto: int, ini: int, fim: int) -> bool:
    if dia_todo(ini, fim):
        return True
    return ini <= minuto < fim if ini < fim else (minuto >= ini or minuto < fim)


def minutos(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def _fuso() -> ZoneInfo:
    return ZoneInfo(get_settings().agenda_fuso)


def _utc(d: datetime | None) -> datetime | None:
    return d.replace(tzinfo=UTC) if d is not None and d.tzinfo is None else d


def dia_da_janela(local: datetime, ini: int, fim: int) -> date:
    """Data da "noite" da agenda. Janela que atravessa a meia-noite (ex.: 22:00-06:00): o trecho depois das 00h
    pertence à janela que começou no dia anterior (SIL17: dispara uma vez por janela e refaz falha depois das 00h)."""
    m = local.hour * 60 + local.minute
    if ini > fim and not dia_todo(ini, fim) and m < fim:
        return local.date() - timedelta(days=1)
    return local.date()


def devido(ag: Agendamento, agora: datetime, ini: int, fim: int) -> bool:
    """`agora` com fuso. Só agendamento ativo roda (SIL15: `forcar` não liga um desligado). Forçado roda fora do
    horário e da janela; senão: dentro da janela, já passou do horário e ainda não resolvido hoje, respeitando
    a espera entre tentativas."""
    if not ag.ativo:
        return False
    if ag.forcar:
        return True
    local = agora.astimezone(_fuso())
    m = local.hour * 60 + local.minute
    if not na_janela(m, ini, fim) or ag.ultima_data == dia_da_janela(local, ini, fim):
        return False
    prox = _utc(ag.proxima_tentativa_em)
    if prox is not None and agora < prox:
        return False
    h = minutos(ag.horario)
    if ini > fim:  # atravessa a meia-noite: compara a posição dentro da janela (22:30 vem antes de 01:00)
        return (m - ini) % 1440 >= (h - ini) % 1440
    return m >= h


def modo_da_vez(ag: Agendamento, agora: datetime) -> str:
    """IN3: completa na primeira vez e a cada AGENDA_COMPLETA_DIAS; incremental no resto."""
    ultima = _utc(ag.ultima_completa_em)
    if ultima is None or agora - ultima >= timedelta(days=get_settings().agenda_completa_dias):
        return "completa"
    return "incremental"


def proxima(ag: Agendamento, agora: datetime, ini: int, fim: int) -> datetime | None:
    """Próximo início previsto (para a tela)."""
    if ag.forcar:
        return agora
    if not ag.ativo:
        return None
    fz = _fuso()
    local = agora.astimezone(fz)
    h, m = (int(x) for x in ag.horario.split(":"))
    agora_min = local.hour * 60 + local.minute
    dia: date = local.date()
    ja_resolvido_hoje = ag.ultima_data == dia
    perdeu_a_janela_hoje = agora_min > h * 60 + m and not na_janela(agora_min, ini, fim)
    if ja_resolvido_hoje or perdeu_a_janela_hoje:
        dia = dia + timedelta(days=1)
    prevista = datetime(dia.year, dia.month, dia.day, h, m, tzinfo=fz)
    if prevista < local:  # passou do horário hoje e ainda está na janela: roda no próximo ciclo
        prevista = local
    prox = _utc(ag.proxima_tentativa_em)
    if prox is not None and prox > prevista:
        prevista = prox.astimezone(fz)
    return prevista


# ---------------------------------------------------------------- origem

def assinatura_origem(leitor, sistema: str) -> dict[str, list]:
    """AG3: {tabela: [linhas, max(campo incremental)]}, uma consulta leve por tabela (guard, sem ler registros).

    Uma tabela que falha (ex.: timeout numa tabela grande) vira [None, "indisponivel"] — assinatura desconhecida, logo
    a carga RODA (não pula) — e a conexão é reaberta para as próximas. Só levanta se TODAS falharem (origem fora),
    com a tabela na mensagem (FalhaLeitura)."""
    from app.services.diagnostico import FalhaLeitura
    pol = politica(sistema)
    tabelas = cc.tabelas_do_conector(sistema)
    out: dict[str, list] = {}
    primeira: FalhaLeitura | None = None
    conn = leitor.abrir()
    try:
        for t in tabelas:
            try:
                out[t] = leitor.assinatura(conn, t, pol, MAPEAMENTOS[(sistema, t)].get("campo_incremental"))
            except Exception as exc:  # noqa: BLE001
                primeira = primeira or FalhaLeitura(t, exc)
                log.warning("assinatura de %s.%s indisponível (%s); a carga roda sem pular", sistema, t,
                            type(exc).__name__)
                out[t] = [None, "indisponivel"]
                try:
                    conn.close()
                except Exception:  # noqa: BLE001
                    pass
                conn = leitor.abrir()
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass
    if primeira is not None and all(v == [None, "indisponivel"] for v in out.values()):
        raise primeira
    return out


def _tem_indisponivel(assin: dict | None) -> bool:
    return any(v == [None, "indisponivel"] for v in (assin or {}).values())


# ---------------------------------------------------------------- alerta

def alertar(assunto: str, corpo: str) -> bool:
    """E-mail opcional. Nunca levanta: sem configuração ou com erro, só registra no log (sem segredo)."""
    s = get_settings()
    para = [p.strip() for p in s.alerta_para.split(",") if p.strip()]
    if not s.smtp_host or not para:
        return False
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = f"[SIGPMF] {assunto}", s.alerta_de or (s.smtp_usuario or "sigpmf"), ", ".join(para)
    msg.set_content(corpo)
    try:
        with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=15) as smtp:
            if s.smtp_starttls:
                smtp.starttls()
            if s.smtp_usuario and s.smtp_senha:
                smtp.login(s.smtp_usuario, s.smtp_senha)
            smtp.send_message(msg)
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("alerta por e-mail não enviado: %s", type(exc).__name__)
        return False


# ---------------------------------------------------------------- execução

EDUCACAO = "educacao"
NOME_EDUCACAO = "Educação (GestaoEscolar + CoreSSO)"
EDUCACAO_SISTEMAS = ("GESTAOESCOLAR",)
# PR-07: job de contagens (J1–J4) na agenda, 05:45 (depois das cargas da noite); nasce desligado (0034)
CONTAGENS = "contagens"
NOME_CONTAGENS = "Contagens automáticas (só agregados)"


def _nome(conector: str) -> str:
    if conector == CONTAGENS:
        return NOME_CONTAGENS
    return NOME_EDUCACAO if conector == EDUCACAO else ccomp.NOMES.get(conector, conector)


def _educacao_ultima(db: Session) -> Execucao | None:
    """Última execução real do conector de educação (amostra ou completa, manual ou pela agenda)."""
    from app.services import carga_gestaoescolar as cge
    return db.scalar(select(Execucao).where(Execucao.tipo.in_((cge.TIPO_AMOSTRA, cge.TIPO_COMPLETA)))
                     .order_by(Execucao.id.desc()).limit(1))


def educacao_em_andamento(db: Session) -> Execucao | None:
    """Trava de concorrência da educação: uma execução (amostra ou completa) ainda em andamento."""
    from app.services import carga_gestaoescolar as cge
    return db.scalar(select(Execucao).where(Execucao.tipo.in_((cge.TIPO_AMOSTRA, cge.TIPO_COMPLETA)),
                                            Execucao.status == "em_andamento").order_by(Execucao.id.desc()).limit(1))


def educacao_recusa(db: Session, agora: datetime | None = None) -> tuple[str, str] | None:
    """(motivo, mensagem) que impede rodar a educação agora; None se pode. A janela vem de
    `carga_gestaoescolar.dentro_da_janela` (um só lugar: a env da janela, quando entrar, vale aqui também)."""
    from app.services import carga_gestaoescolar as cge
    if educacao_em_andamento(db) is not None:
        return "ocupado", "Já existe uma carga de educação em andamento."
    if not cge.amostra_ok(db):
        return "sem_amostra", cge.SEM_AMOSTRA
    try:  # SIGPMF_EDUCACAO_JANELA_NOTURNA=false desliga a trava da janela aqui também (#36)
        cge.checa_janela(agora or datetime.now(UTC))
    except cge.CargaRecusada as exc:
        return "fora_janela", str(exc)
    return None


def _executar_educacao(db: Session, ag: Agendamento, agora: datetime, leitor=None) -> str:
    """Mesma carga da rota POST /educacao/conector/completa (cge.completa); a agenda só a dispara."""
    from app.connectors.gestaoescolar.consultas import ConfigGE, LeitorGE
    from app.services import carga_gestaoescolar as cge
    recusa = educacao_recusa(db, agora)
    if recusa:
        return _encerra(db, ag, "ocupada" if recusa[0] == "ocupado" else "bloqueada", recusa[1], agora,
                        resolvido=False)
    cfg = ConfigGE.do_ambiente(get_settings())
    try:
        if leitor is None:
            cfg.valida()
            with LeitorGE(cfg) as lt:
                ex = cge.completa(db, lt, cfg, USUARIO, agora=agora)
        else:
            ex = cge.completa(db, leitor, cfg, USUARIO, agora=agora)
    except (PermissionError, cge.CargaRecusada) as exc:
        return _encerra(db, ag, "bloqueada", cge.mensagem_segura(exc), agora, resolvido=False)
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        return _encerra(db, ag, "falha", cge.mensagem_segura(exc), agora, resolvido=False)
    ag.ultima_execucao_id = ex.id
    db.commit()
    if ex.status == "sucesso":
        ag.ultima_completa_em = agora
        _alerta_tabela_vazia(ag, ex)
        return _encerra(db, ag, "sucesso", ex.mensagem or "Carga de educação concluída.", agora, resolvido=True)
    if ex.status == "parcial":
        return _encerra(db, ag, "parcial", ex.mensagem or "Carga de educação parcial.", agora, resolvido=True)
    return _encerra(db, ag, "falha", ex.mensagem or f"Carga de educação terminou como {ex.status}.", agora,
                    resolvido=False)


def _executar_contagens(db: Session, ag: Agendamento, agora: datetime) -> str:
    """Roda o job J1–J4 (só leitura, só agregados). Falha vira mensagem fixa, nunca str(exc)."""
    from app.contagens import job
    try:
        ex = job.executar(db, USUARIO)
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        return _encerra(db, ag, "falha", f"Contagens não concluídas ({type(exc).__name__}).", agora, resolvido=False)
    if ex.status == "concluida":
        return _encerra(db, ag, "sucesso", f"Contagens concluídas (execução {ex.id}).", agora, resolvido=True)
    if ex.status == "parcial":
        return _encerra(db, ag, "parcial", f"Contagens parciais (execução {ex.id}): veja os códigos de falha.", agora,
                        resolvido=True)
    return _encerra(db, ag, "bloqueada", f"Contagens recusadas: {ex.codigo or ex.status}.", agora, resolvido=True)


def _alerta_tabela_vazia(ag: Agendamento, ex: Execucao) -> None:
    """Sucesso com tabela vazia: o aviso fica na execução (API `alerta`) e sai por e-mail se houver SMTP."""
    a = ex.alerta_publico()
    if a:
        alertar(f"Carga de {_nome(ag.conector)}: tabela(s) com 0 linhas", f"{a['texto']}\n\nVeja o Monitor de cargas no SIGPMF.")


def _encerra(db: Session, ag: Agendamento, status: str, mensagem: str, agora: datetime, resolvido: bool) -> str:
    s = get_settings()
    if len(mensagem) > MAX_MENSAGEM:
        mensagem = mensagem[: MAX_MENSAGEM - 1] + "…"
    ag.ultimo_status, ag.ultima_mensagem, ag.ultimo_fim_em, ag.forcar = status, mensagem, datetime.now(UTC), False
    ini, fim = janela(s.agenda_janela)
    hoje = dia_da_janela(agora.astimezone(_fuso()), ini, fim)
    if resolvido:
        ag.ultima_data, ag.tentativas, ag.proxima_tentativa_em = hoje, 0, None
    else:
        ag.tentativas = (ag.tentativas or 0) + 1
        if ag.tentativas >= s.agenda_tentativas:
            ag.ultima_data, ag.tentativas, ag.proxima_tentativa_em = hoje, 0, None
            mensagem = f"{mensagem} Tentativas esgotadas: fica para o próximo dia."
            if len(mensagem) > MAX_MENSAGEM:
                mensagem = mensagem[: MAX_MENSAGEM - 1] + "…"
            ag.ultima_mensagem = mensagem
        else:
            ag.proxima_tentativa_em = agora + timedelta(minutes=s.agenda_reespera_min)
    db.commit()
    audita(db, USUARIO, f"agenda_{status}", f"agendamento:{ag.conector}",
           {"execucao_id": ag.ultima_execucao_id, "tentativas": ag.tentativas, "resolvido": resolvido})
    if status in ("falha", "interrompida", "demorada"):
        alertar(f"Carga de {_nome(ag.conector)}: {STATUS_TEXTO.get(status, status)}",
                f"{mensagem}\n\nVeja o Monitor de cargas no SIGPMF.")
    return status


def aguardar(db: Session, ex_id: int, parar: threading.Event | None = None) -> Execucao:
    """Espera a carga (thread em background) sair de em_andamento, com limite AGENDA_ESPERA_MAX_H."""
    s = get_settings()
    limite = time.monotonic() + s.agenda_espera_max_h * 3600
    while True:
        db.expire_all()
        ex = db.get(Execucao, ex_id)
        if ex is None or ex.status != "em_andamento" or time.monotonic() > limite or (parar and parar.is_set()):
            return ex
        (parar or threading.Event()).wait(min(s.agenda_intervalo_s, 30))


def executar(db: Session, ag: Agendamento, agora: datetime | None = None, leitor=None, esperar=aguardar,
             parar: threading.Event | None = None) -> str:
    """Roda um agendamento até o fim e devolve o status final (sem levantar exceção)."""
    s = get_settings()
    agora = agora or datetime.now(UTC)
    conector, nome = ag.conector, _nome(ag.conector)
    anterior = ag.ultimo_status
    ag.ultimo_inicio_em, ag.ultimo_status, ag.ultima_mensagem = datetime.now(UTC), "em_andamento", None
    db.commit()
    if conector == EDUCACAO:
        return _executar_educacao(db, ag, agora, leitor=leitor)
    if conector == CONTAGENS:
        return _executar_contagens(db, ag, agora)
    motivo = ccomp.motivo_bloqueio(db, conector)
    if motivo:
        return _encerra(db, ag, "bloqueada", f"{motivo} (a agenda só repete cargas já liberadas).", agora, resolvido=True)
    sistema = cc.CONECTORES[conector][0]
    assin = None
    if s.agenda_pular_sem_mudanca:
        try:
            assin = assinatura_origem(leitor or ccomp.leitor_padrao(conector, db), sistema)
        except Exception as exc:  # noqa: BLE001 - nunca devolve host/usuário; só o motivo seguro
            from app.services.diagnostico import categoria
            from app.services.diagnostico import motivo as motivo_seguro
            c = categoria(exc)
            return _encerra(db, ag, "falha", f"Não foi possível consultar a origem de {nome}: "
                                             f"{motivo_seguro(c['classe'], c['numero'], c['tabela'], c['objeto'])}.",
                            agora, resolvido=False)
        if ag.assinatura_origem == assin and anterior in RESOLVIDOS and not _tem_indisponivel(assin):
            return _encerra(db, ag, "sem_mudanca", f"A origem de {nome} não mudou desde a última carga: nada foi lido.",
                            agora, resolvido=True)
    modo = modo_da_vez(ag, agora)
    try:
        ex = ccomp.iniciar(db, USUARIO, conector, modo=modo)
    except ccomp.Bloqueado as exc:
        return _encerra(db, ag, "bloqueada", str(exc), agora, resolvido=False)
    except cc.Conflito:
        return _encerra(db, ag, "ocupada", f"Já existe uma carga de {nome} em andamento; a agenda tenta de novo depois.",
                        agora, resolvido=False)
    except Exception as exc:  # noqa: BLE001 - o agendamento nunca fica "em_andamento" sem carga
        db.rollback()
        return _encerra(db, ag, "falha", f"Não foi possível iniciar a carga de {nome} ({type(exc).__name__}).",
                        agora, resolvido=False)
    ag.ultima_execucao_id = ex.id
    db.commit()
    ex = esperar(db, ex.id, parar)
    db.refresh(ag)
    if ex.status == "em_andamento":
        return _encerra(db, ag, "demorada", f"A carga de {nome} passou de {s.agenda_espera_max_h} h e segue em "
                                            "andamento; a agenda não inicia outra até ela terminar.", agora, resolvido=True)
    if ex.status == "sucesso":
        _alerta_tabela_vazia(ag, ex)
        ag.assinatura_origem = assin
        if modo == "completa":
            ag.ultima_completa_em = agora
        return _encerra(db, ag, "sucesso", f"Carga {modo} de {nome} concluída: {ex.lidos} lidas, "
                                           f"{(ex.novos or 0) + (ex.atualizados or 0)} gravadas"
                                           + (" (prata e ouro mantidos: nada mudou)." if ccomp.MSG_CAMADAS_MANTIDAS in (ex.mensagem or "")
                                              else "."), agora, resolvido=True)
    if ex.status == ccomp.STATUS_PARCIAL:  # teto de lotes: não é completa (sem ultima_completa_em nem assinatura)
        return _encerra(db, ag, "parcial", ex.mensagem or f"Carga de {nome} parcial (teto de lotes).", agora,
                        resolvido=True)
    return _encerra(db, ag, ex.status if ex.status in STATUS_TEXTO else "falha",
                    ex.mensagem or f"Carga de {nome} terminou como {ex.status}.", agora, resolvido=False)


def ciclo(db: Session, agora: datetime | None = None, leitores: dict | None = None, esperar=aguardar,
          parar: threading.Event | None = None) -> list[tuple[str, str]]:
    """Uma passada: roda, em ordem e um por vez, os agendamentos devidos agora."""
    s = get_settings()
    ini, fim = janela(s.agenda_janela)
    relogio_fixo = agora is not None  # testes passam a hora; em produção ela anda durante as cargas
    feitos = []
    for ag in list(db.scalars(select(Agendamento).order_by(Agendamento.ordem, Agendamento.id))):
        if parar is not None and parar.is_set():
            break
        momento = agora if relogio_fixo else datetime.now(UTC)
        if (ag.conector not in ccomp.CONECTORES_COMPLETA and ag.conector not in (EDUCACAO, CONTAGENS)) or not devido(ag, momento, ini, fim):
            continue
        leitor = (leitores or {}).get(ag.conector)
        feitos.append((ag.conector, executar(db, ag, momento, leitor=leitor, esperar=esperar, parar=parar)))
    # PL3: retenção uma vez por dia, depois das cargas, só dentro da janela e com a agenda ligada
    from app.services import retencao
    momento = agora if relogio_fixo else datetime.now(UTC)
    local = momento.astimezone(_fuso())
    if (s.agenda_habilitada and not (parar is not None and parar.is_set())
            and na_janela(local.hour * 60 + local.minute, ini, fim) and not retencao.ja_rodou_hoje(db, momento)):
        feitos.append(("retencao", retencao.executar(db, USUARIO, momento).status))
    return feitos


class Agendador:
    """Thread do backend. Só um processo agenda (advisory lock de sessão); os outros ficam de reserva."""

    def __init__(self) -> None:
        self._parar = threading.Event()
        self._thread: threading.Thread | None = None

    def iniciar(self) -> None:
        self._thread = threading.Thread(target=self._rodar, daemon=True, name="agenda-cargas")
        self._thread.start()

    def parar(self) -> None:
        self._parar.set()

    def _rodar(self) -> None:
        intervalo = get_settings().agenda_intervalo_s
        try:
            janela(get_settings().agenda_janela)
        except AgendaInvalida as exc:
            log.error("agenda não iniciada: %s", exc)
            return
        with get_engine().connect() as lider:
            sou_lider = False
            while not self._parar.is_set():
                sou_lider = bool(lider.execute(text("select pg_try_advisory_lock(:k)"), {"k": CHAVE_LIDER}).scalar())
                lider.commit()
                if sou_lider:
                    break
                self._parar.wait(intervalo)
            if not sou_lider:
                return
            log.info("agenda de cargas ativa (janela %s)", get_settings().agenda_janela)
            try:
                while not self._parar.is_set():
                    db = SessionLocal()
                    try:
                        ciclo(db, parar=self._parar)
                    except Exception as exc:  # noqa: BLE001 - o laço nunca morre
                        log.warning("ciclo da agenda falhou: %s", type(exc).__name__)
                    finally:
                        db.close()
                    self._parar.wait(intervalo)
            finally:
                # A conexão volta ao pool (não fecha): sem o unlock, a trava de líder ficaria presa nela.
                lider.execute(text("select pg_advisory_unlock(:k)"), {"k": CHAVE_LIDER})
                lider.commit()


# ---------------------------------------------------------------- tela (API)

def _execucao(db: Session, ex_id: int | None) -> dict | None:
    ex = db.get(Execucao, ex_id) if ex_id else None
    if ex is None:
        return None
    return {"id": ex.id, "status": ex.status, "etapa": ex.etapa, "modo": ex.modo or "completa", "iniciado_em": _utc(ex.iniciado_em),
            "finalizado_em": _utc(ex.finalizado_em), "lidos": ex.lidos, "gravados": (ex.novos or 0) + (ex.atualizados or 0),
            "processadas_etapa": ex.processadas_etapa, "total_etapa": ex.total_etapa, "alerta": ex.alerta_publico()}


def estado(db: Session, agora: datetime | None = None) -> dict:
    s = get_settings()
    ini, fim = janela(s.agenda_janela)
    agora = agora or datetime.now(UTC)
    itens = []
    for ag in db.scalars(select(Agendamento).order_by(Agendamento.ordem, Agendamento.id)):
        sistema = cc.CONECTORES.get(ag.conector, (None,))[0]
        atual = ccomp.ativa(db, sistema) if sistema else None
        p = proxima(ag, agora, ini, fim)
        extra: dict = {}
        if ag.conector == EDUCACAO:
            atual = educacao_em_andamento(db)
            ult = _educacao_ultima(db)
            recusa = educacao_recusa(db, agora)
            extra = {"ultima_execucao_real": _execucao(db, ult.id) if ult else None,
                     "pode_executar_agora": recusa is None,
                     "motivo_executar_agora": recusa[1] if recusa else None,
                     "motivo_executar_agora_codigo": recusa[0] if recusa else None}
        itens.append({**extra,
            "conector": ag.conector, "nome": _nome(ag.conector), "horario": ag.horario, "ativo": ag.ativo,
            "ordem": ag.ordem, "forcar": ag.forcar, "ultimo_status": ag.ultimo_status,
            "ultimo_status_texto": STATUS_TEXTO.get(ag.ultimo_status or "", ag.ultimo_status),
            "ultima_mensagem": ag.ultima_mensagem, "ultimo_inicio_em": _utc(ag.ultimo_inicio_em),
            "ultimo_fim_em": _utc(ag.ultimo_fim_em), "tentativas": ag.tentativas,
            "proxima_em": p, "proximo_modo": modo_da_vez(ag, agora), "ultima_completa_em": _utc(ag.ultima_completa_em),
            "horario_fora_da_janela": not na_janela(minutos(ag.horario), ini, fim),
            "ultima_execucao": _execucao(db, ag.ultima_execucao_id),
            "em_andamento": _execucao(db, atual.id) if atual else None,
            "atualizado_por": ag.atualizado_por})
    historico = [{"conector": EDUCACAO if e.sistema in EDUCACAO_SISTEMAS
                  else next((k for k, v in cc.CONECTORES.items() if v[0] == e.sistema), e.sistema),
                  **_execucao(db, e.id)}
                 for e in db.scalars(select(Execucao).where(Execucao.usuario == USUARIO,
                                                            Execucao.iniciado_em >= agora - timedelta(hours=24))
                                     .order_by(Execucao.id))]
    return {"habilitada": s.agenda_habilitada, "janela": s.agenda_janela, "dia_todo": dia_todo(ini, fim),
            "completa_a_cada_dias": s.agenda_completa_dias,
            "fuso": s.agenda_fuso, "pular_sem_mudanca": s.agenda_pular_sem_mudanca,
            "alerta_email": bool(s.smtp_host and s.alerta_para), "agora": agora, "itens": itens, "historico": historico}


class AgendaDesligada(Exception):
    """#33 B2: "Executar agora" num agendamento desligado (inclusive desligado em paralelo)."""


def _agendamento(db: Session, conector: str, travar: bool = False) -> Agendamento:
    q = select(Agendamento).where(Agendamento.conector == conector)
    if travar:  # #33 B2: trava a linha; um forcar() concorrente espera e reavalia "WHERE ativo"
        q = q.with_for_update()
    ag = db.scalar(q)
    if ag is None:
        raise LookupError(conector)
    return ag


CHAVE_TRAVA_SIL = zlib.crc32(b"amostra:SIL")  # mesma trava das cargas SIL e do scripts/reset_sil.py
MOTIVO_SIL_TRAVADA = "sil_trava_ocupada"
MSG_SIL_TRAVADA = ("A SIL está com uma carga ou um reset em andamento (trava amostra:SIL). "
                   "Espere terminar para ligar o agendamento.")


class AgendaTravada(RuntimeError):
    """B1 (#38 follow-up): ligar a agenda da SIL com a trava amostra:SIL ocupada (carga ou reset)."""


def atualizar(db: Session, conector: str, usuario: str, horario: str | None = None, ativo: bool | None = None,
              ordem: int | None = None) -> Agendamento:
    # B1: antes da trava da linha (o reset segura a linha FOR UPDATE e a trava); try = sem esperar
    if ativo and conector == "sil" and not db.execute(
            text("select pg_try_advisory_xact_lock(:k)"), {"k": CHAVE_TRAVA_SIL}).scalar():
        db.rollback()
        raise AgendaTravada(MOTIVO_SIL_TRAVADA)
    ag = _agendamento(db, conector, travar=True)
    antes = {"horario": ag.horario, "ativo": ag.ativo, "ordem": ag.ordem}
    if horario is not None:
        if not HORARIO.match(horario):
            raise AgendaInvalida("Horário deve ser HH:MM (00:00 a 23:59).")
        if horario != ag.horario:
            ag.ultima_data = None  # novo horário vale já hoje
        ag.horario = horario
    forcar_cancelado = False
    if ativo is not None:
        ag.ativo = ativo
        if ativo:
            ag.tentativas, ag.proxima_tentativa_em = 0, None
        elif ag.forcar:  # #33 M1: desligar cancela um "Executar agora" pendente (não dispara ao religar)
            ag.forcar, forcar_cancelado = False, True
    if ordem is not None:
        ag.ordem = ordem
    ag.atualizado_por, ag.atualizado_em = usuario, datetime.now(UTC)
    db.commit()
    audita(db, usuario, "agenda_alterada", f"agendamento:{conector}",
           {"antes": antes, "depois": {"horario": ag.horario, "ativo": ag.ativo, "ordem": ag.ordem}})
    if forcar_cancelado:
        audita(db, usuario, "agenda_executar_agora_cancelado", f"agendamento:{conector}",
               {"motivo": "forcar_cancelado_desligado", "conector": conector})
    return ag


def forcar(db: Session, conector: str, usuario: str) -> Agendamento:
    """#33 B2: forcar=true numa única UPDATE ... WHERE ativo (atômica). Se outro pedido desligou antes, nenhuma
    linha muda e sobe AgendaDesligada (409); se desligar vier depois, ele trava a linha e cancela o forcar."""
    _agendamento(db, conector)  # 404 se não existe
    feito = db.execute(update(Agendamento).where(Agendamento.conector == conector, Agendamento.ativo.is_(True))
                       .values(forcar=True, tentativas=0, proxima_tentativa_em=None, atualizado_por=usuario,
                               atualizado_em=datetime.now(UTC)).returning(Agendamento.id)).first()
    db.commit()
    if feito is None:
        raise AgendaDesligada(conector)
    ag = _agendamento(db, conector)
    db.refresh(ag)
    audita(db, usuario, "agenda_executar_agora", f"agendamento:{conector}")
    return ag
