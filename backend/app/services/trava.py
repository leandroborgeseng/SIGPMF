"""CC12: trava de carga por sistema (amostra e completa compartilham o advisory lock) e execuções órfãs.

- A trava real é o advisory lock de sessão `crc32("amostra:<SISTEMA>")`: morre com o processo.
- A linha `controle.execucoes` em_andamento pode sobrar depois de um reinício. Se a trava está livre (nenhum processo
  vivo) ou a execução está sem atividade há mais de SIGPMF_CARGA_ALERTA_MIN, ela vira "interrompida" (auditado)
  quando alguém pede uma nova carga. Nada é apagado (bronze, checkpoints e logs ficam).
- Sinal de vida da amostra: `atualizado_em` gravado numa conexão separada (visível durante a transação)."""
import zlib
from datetime import UTC, datetime

from sqlalchemy import select, text, update
from sqlalchemy.orm import Session

from app.core.audit import audita
from app.core.config import get_settings
from app.core.db import get_engine
from app.models.controle import Execucao, Log

NOMES = {"saude": "Saúde", "cemiterio": "Cemitério", "iss": "ISS"}
TIPO_COMPLETA = "carga_completa_conector"
TIPOS_AMOSTRA = ("amostra_conector", "carga_municipe_amostra")
TIPOS_CARGA = (*TIPOS_AMOSTRA, TIPO_COMPLETA)
MSG_INTERROMPIDA = ("Execução interrompida: sem processo vivo ou sem atividade além do limite. "
                    "A bronze foi mantida; inicie a carga de novo.")


def chave_lock(sistema: str) -> int:
    return zlib.crc32(f"amostra:{sistema}".encode())


def trava_livre(db: Session, sistema: str) -> bool:
    """True se nenhum processo segura a trava do sistema."""
    with db.get_bind().connect() as c:
        livre = bool(c.execute(text("select pg_try_advisory_lock(:k)"), {"k": chave_lock(sistema)}).scalar())
        if livre:
            c.execute(text("select pg_advisory_unlock(:k)"), {"k": chave_lock(sistema)})
        c.commit()
    return livre


def sinal_de_vida(ex_id: int) -> None:
    """Heartbeat (conexão separada, commit próprio)."""
    with get_engine().begin() as c:
        c.execute(update(Execucao).where(Execucao.id == ex_id).values(atualizado_em=datetime.now(UTC)))


def _utc(d: datetime | None) -> datetime | None:
    return d.replace(tzinfo=UTC) if d is not None and d.tzinfo is None else d


def ultima_atividade(ex: Execucao) -> datetime | None:
    return _utc(ex.atualizado_em) or _utc(ex.iniciado_em)


def parada_min(ex: Execucao) -> float:
    visto = ultima_atividade(ex)
    return 0.0 if visto is None else max((datetime.now(UTC) - visto).total_seconds() / 60, 0.0)


def tipo_rotulo(ex: Execucao) -> str:
    return "carga completa" if ex.tipo == TIPO_COMPLETA else "amostra"


def em_andamento(db: Session, sistema: str) -> list[Execucao]:
    return list(db.scalars(select(Execucao).where(Execucao.tipo.in_(TIPOS_CARGA), Execucao.sistema == sistema,
                                                  Execucao.status == "em_andamento").order_by(Execucao.id.desc())))


def _hora(d: datetime | None) -> str:
    if d is None:
        return "-"
    from zoneinfo import ZoneInfo
    return d.astimezone(ZoneInfo("America/Sao_Paulo")).strftime("%d/%m %H:%M")


def mensagem_viva(ex: Execucao, nome: str) -> str:
    return (f"Já existe uma {tipo_rotulo(ex)} de {nome} em andamento desde {_hora(_utc(ex.iniciado_em))} "
            f"(última atividade {_hora(ultima_atividade(ex))}).")


def interromper(db: Session, ex: Execucao, usuario: str, motivo: str, automatico: bool, ip: str | None = None) -> None:
    """Marca a execução como interrompida (e pede cancelamento a quem ainda estiver vivo). Auditado; nada apagado."""
    anterior = ex.status
    if ex.tipo == TIPO_COMPLETA:
        # Completa: volta a 'pausada' (como antes do CC12), que mantém "Retomar carga" do checkpoint na tela.
        ex.status, ex.pausa_motivo = "pausada", f"Carga interrompida ({motivo}). Retome a carga."
        ex.finalizado_em, ex.tabela_atual = datetime.now(UTC), None
        msg = f"Carga completa pausada: {motivo}. A bronze e o checkpoint foram mantidos; retome a carga."
    else:
        ex.status, ex.mensagem, ex.finalizado_em, ex.tabela_atual = "interrompida", MSG_INTERROMPIDA, datetime.now(UTC), None
        msg = f"{MSG_INTERROMPIDA} Motivo: {motivo}"
    ex.cancelar = True
    db.add(Log(execucao_id=ex.id, nivel="WARN", mensagem=msg))
    audita(db, usuario, "execucao_interrompida", f"execucao:{ex.id}",
           {"tipo": ex.tipo, "sistema": ex.sistema, "status_anterior": anterior, "motivo": motivo,
            "automatico": automatico, "parada_min": round(parada_min(ex), 1)}, ip, commit=False)


def limpa_orfas(db: Session, sistema: str, usuario: str, motivo: str = "sem processo vivo (trava livre)") -> int:
    """Chame SEGURANDO a trava: toda execução em_andamento do sistema é órfã (ninguém mais pode estar rodando)."""
    n = 0
    for ex in em_andamento(db, sistema):
        interromper(db, ex, usuario, motivo, automatico=True)
        n += 1
    if n:
        db.commit()
    return n


def estado(db: Session, sistema: str, nome: str) -> dict | None:
    """Execução que bloqueia o sistema (para a tela): tipo, início, última atividade e se pode ser interrompida."""
    exs = em_andamento(db, sistema)
    if not exs:
        return None
    ex = exs[0]
    viva = not trava_livre(db, sistema)
    alerta = get_settings().carga_alerta_min
    p = parada_min(ex)
    return {"execucao_id": ex.id, "tipo": "completa" if ex.tipo == TIPO_COMPLETA else "amostra",
            "tipo_rotulo": tipo_rotulo(ex), "iniciado_em": _utc(ex.iniciado_em), "atualizado_em": ultima_atividade(ex),
            "viva": viva, "parada_min": round(p, 1), "alerta_min": alerta,
            "parada": (not viva) or p > alerta, "mensagem": mensagem_viva(ex, nome)}


def interrompe_se_parada(db: Session, sistema: str, usuario: str) -> int:
    """Pedido de nova carga com a trava ocupada: execução sem atividade além do limite vira interrompida (auditado)
    e recebe pedido de cancelamento. Retorna quantas foram interrompidas."""
    alerta = get_settings().carga_alerta_min
    n = 0
    for ex in em_andamento(db, sistema):
        if parada_min(ex) > alerta:
            interromper(db, ex, usuario, f"sem atividade há mais de {alerta} min", automatico=True)
            n += 1
    if n:
        db.commit()
    return n


def conflito(db: Session, sistema: str, conector: str, usuario: str) -> str:
    """Trava ocupada: interrompe a execução parada (se houver) e devolve a mensagem para o 409."""
    nome = NOMES.get(conector, conector)
    exs = em_andamento(db, sistema)
    if exs and interrompe_se_parada(db, sistema, usuario):
        marcada = "pausada (retomável do checkpoint)" if exs[0].tipo == TIPO_COMPLETA else "interrompida"
        return (f"A {tipo_rotulo(exs[0])} de {nome} estava sem atividade desde {_hora(ultima_atividade(exs[0]))} e foi "
                f"marcada como {marcada}. O processo antigo ainda segura a trava; tente de novo em instantes.")
    if exs:
        return mensagem_viva(exs[0], nome)
    return f"Já existe uma carga de {nome} em andamento."
