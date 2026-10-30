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

NOMES = {"saude": "Saúde", "cemiterio": "Cemitério", "iss": "ISS", "iptu": "IPTU", "divida": "Dívida ativa",
         "financeiro": "Financeiro", "sil": "API SIL"}
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


def sinal_de_vida(ex_id: int, etapa: str | None = None, processadas: int | None = None,
                  total: int | None = None) -> None:
    """Heartbeat (conexão separada, commit próprio). Com `etapa`, grava também etapa e contador (como a completa)."""
    vals = {"atualizado_em": datetime.now(UTC), "ultimo_sinal_em": datetime.now(UTC)}
    if etapa is not None:
        vals.update(etapa=etapa, processadas_etapa=processadas, total_etapa=total)
    with get_engine().begin() as c:
        c.execute(update(Execucao).where(Execucao.id == ex_id).values(**vals))


CHAVE_CAMADAS = zlib.crc32(b"camadas:prata+ouro")


def aguarda_camadas(db: Session, ex_id: int | None = None, espera_s: float = 2.0, timeout_s: float | None = None) -> None:
    """Prata e ouro são reconstruídas para TODOS os sistemas (DELETE + INSERT). A trava por sistema (#20) não impede
    que, por exemplo, uma amostra de Saúde e uma de Cemitério reconstruam ao mesmo tempo: a segunda esperaria as
    linhas da primeira e depois gravaria por cima (duplicidade/deadlock). Esta trava de TRANSAÇÃO (some no commit ou
    rollback) serializa as reconstruções; enquanto espera, mantém o sinal de vida da execução."""
    import time
    t0 = time.monotonic()
    while not db.execute(text("select pg_try_advisory_xact_lock(:k)"), {"k": CHAVE_CAMADAS}).scalar():
        if ex_id is not None:
            sinal_de_vida(ex_id)
        if timeout_s is not None and time.monotonic() - t0 > timeout_s:
            raise TimeoutError("outra reconstrução de prata/ouro em andamento")
        time.sleep(espera_s)


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
    """Amostra -> interrompida; completa -> pausada ou interrompida (marca_orfa) (e pede cancelamento a quem ainda estiver vivo). Auditado; nada apagado."""
    anterior = ex.status
    if ex.tipo == TIPO_COMPLETA:
        from app.services.carga_completa import marca_orfa
        msg = marca_orfa(ex, motivo)
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


def conflito(db: Session, sistema: str, conector: str, usuario: str) -> str:
    """Trava ocupada = processo vivo: NUNCA interrompe sozinho (mesmo sem sinal de vida). Devolve a mensagem do 409
    com a execução que bloqueia; só o admin interrompe, pelo POST .../interromper com motivo."""
    nome = NOMES.get(conector, conector)
    exs = em_andamento(db, sistema)
    if not exs:
        return f"Já existe uma carga de {nome} em andamento."
    msg = mensagem_viva(exs[0], nome)
    alerta = get_settings().carga_alerta_min
    if parada_min(exs[0]) > alerta:
        msg += f" Sem atividade há mais de {alerta} min: um admin pode interrompê-la informando o motivo."
    return msg
