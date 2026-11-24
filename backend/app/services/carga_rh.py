"""Carga do RH (RH1): Portal da Transparência -> silver.rh_remuneracao, um mês por vez, em background.

- Sem bronze: o CSV inteiro é público e pode ser baixado de novo; duplicar ~1 milhão de linhas em JSONB só gastaria disco.
  O controle guarda o SHA-256 de cada mês (conteúdo igual não regrava).
- Primeira carga: de 2012 (ano inicial) até o mês atual, com pausa entre os meses, para não pesar o portal.
- Cargas seguintes: mês já carregado é pulado, salvo os últimos 3 meses e o décimo terceiro recente, que são conferidos
  de novo (podem sair ou mudar depois). `forcar` confere tudo.
- Cada mês é tudo ou nada e é confirmado sozinho: parou no meio, a próxima carga continua de onde estava.
- 3 falhas seguidas param a execução (portal fora do ar): termina `parcial`. Mensagens só com mês e contagens.
"""
import logging
import threading
import time
from datetime import UTC, date, datetime, timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.connectors.portal_rh import servidores as sv
from app.models.controle import Execucao, Log
from app.models.rh import MesRh
from app.silver import financas, rh

log = logging.getLogger("sigpmf.rh")
TIPO = "carga_rh_portal"
USUARIO_AGENDA = "agenda"
ORFA_MIN = 30
FALHAS_SEGUIDAS = 3
RECONFERE_MESES = 3


class CargaRecusada(RuntimeError):
    """Já existe carga do RH em andamento."""


def _rotulo(ano: int, mes: int) -> str:
    return f"13º/{ano}" if mes == sv.MES_13 else f"{mes:02d}/{ano}"


def meses_ate(ano_inicial: int, hoje: date) -> list[tuple[int, int]]:
    """Todos os meses do ano inicial até hoje (em ordem), com o 13º depois de dezembro do mesmo ano."""
    out = []
    for ano in range(ano_inicial, hoje.year + 1):
        for mes in range(1, 14):
            if mes == sv.MES_13 and ano == hoje.year and hoje.month < 11:
                continue  # o 13º sai no fim do ano
            if mes != sv.MES_13 and (ano, mes) > (hoje.year, hoje.month):
                continue
            out.append((ano, mes))
    return out


def reconfere(ano: int, mes: int, hoje: date) -> bool:
    if mes == sv.MES_13:
        return ano >= hoje.year - 1
    return (hoje.year * 12 + hoje.month) - (ano * 12 + mes) < RECONFERE_MESES


def _fim(db: Session, ex: Execucao, status: str, mensagem: str) -> Execucao:
    ex.status, ex.mensagem, ex.etapa = status, mensagem, "concluida"
    ex.tabela_atual, ex.finalizado_em, ex.atualizado_em = None, datetime.now(UTC), datetime.now(UTC)
    db.commit()
    return ex


def em_andamento(db: Session) -> Execucao | None:
    """A execução sem sinal de vida há mais de ORFA_MIN minutos vira `interrompida` (o servidor parou no meio)."""
    ex = db.scalar(select(Execucao).where(Execucao.tipo == TIPO, Execucao.status == "em_andamento").order_by(Execucao.id.desc()).limit(1))
    if ex is not None and (ex.atualizado_em or ex.iniciado_em) < datetime.now(UTC) - timedelta(minutes=ORFA_MIN):
        _fim(db, ex, "interrompida", "Carga de RH interrompida (o servidor parou). Carregue de novo: ela continua de onde estava.")
        return None
    return ex


def ultima(db: Session) -> Execucao | None:
    return db.scalar(select(Execucao).where(Execucao.tipo == TIPO).order_by(Execucao.id.desc()).limit(1))


def _nova(db: Session, usuario: str | None) -> Execucao:
    if em_andamento(db) is not None:
        raise CargaRecusada("Já existe uma carga de RH em andamento.")
    ex = Execucao(tipo=TIPO, sistema=sv.SISTEMA, status="em_andamento", usuario=usuario, modo="incremental", etapa="leitura",
                  atualizado_em=datetime.now(UTC))
    db.add(ex)
    db.commit()
    return ex


def iniciar_em_fundo(db: Session, usuario: str, forcar: bool = False) -> Execucao:
    ex = _nova(db, usuario)
    disparar(ex.id, forcar)
    return ex


def disparar(ex_id: int, forcar: bool = False) -> None:
    """Background (thread daemon). Os testes trocam por execução síncrona."""
    threading.Thread(target=rodar, args=(ex_id, forcar), daemon=True, name=f"rh-{ex_id}").start()


def rodar(ex_id: int, forcar: bool = False) -> None:
    from app.core.db import SessionLocal
    db = SessionLocal()
    try:
        ex = db.get(Execucao, ex_id)
        if ex is not None and ex.status == "em_andamento":
            executar(db, ex.usuario, forcar=forcar, ex=ex)
    except Exception:  # noqa: BLE001
        log.exception("RH: execução %s caiu fora do laço", ex_id)
    finally:
        db.close()


def _carrega_mes(db: Session, ex: Execucao, cliente: httpx.Client, url: str, ano: int, mes: int, reg: MesRh | None) -> str:
    """'novo', 'igual' ou 'vazio'. Confirma o mês inteiro ou nada."""
    conteudo = sv.baixa_mes(cliente, url, ano, mes)
    linhas = sv.le_csv(conteudo) if conteudo else []
    h = sv.sha256(conteudo) if linhas else None
    if reg is not None and reg.sha256 == h:
        return "igual" if h else "vazio"
    # mesma transação: valor fora do padrão desfaz a troca e o que já havia continua (rollback no chamador)
    n = rh.troca_mes(db, ano, mes, linhas)
    if reg is None:
        reg = MesRh(ano=ano, mes=mes)
        db.add(reg)
    reg.sha256, reg.linhas, reg.execucao_id, reg.carregado_em = h, n, ex.id, datetime.now(UTC)
    ex.lidos, ex.novos = (ex.lidos or 0) + n, (ex.novos or 0) + n
    db.commit()
    return "novo" if n else "vazio"


def executar(db: Session, usuario: str | None = USUARIO_AGENDA, cfg: sv.ConfigPortalRh | None = None,
             cliente: httpx.Client | None = None, forcar: bool = False, ex: Execucao | None = None,
             hoje: date | None = None) -> Execucao:
    cfg = cfg or sv.ConfigPortalRh.do_ambiente()
    hoje = hoje or date.today()
    ex = ex or _nova(db, usuario)
    proprio = cliente is None
    cliente = cliente or sv.cliente_http()
    feitos = {r.ano * 100 + r.mes: r for r in db.scalars(select(MesRh))}
    novos, falhas, seguidas = [], [], 0
    try:
        for ano, mes in meses_ate(cfg.ano_inicial, hoje):
            reg = feitos.get(ano * 100 + mes)
            if reg is not None and not forcar and not reconfere(ano, mes, hoje):
                continue
            ex.tabela_atual, ex.atualizado_em, ex.ultimo_sinal_em = _rotulo(ano, mes), datetime.now(UTC), datetime.now(UTC)
            db.commit()
            try:
                if _carrega_mes(db, ex, cliente, cfg.url, ano, mes, reg) == "novo":
                    novos.append((ano, mes))
                seguidas = 0
            except (sv.ErroPortal, financas.LinhaInvalida, PermissionError) as exc:
                db.rollback()
                falhas.append(_rotulo(ano, mes))
                seguidas += 1
                db.add(Log(execucao_id=ex.id, nivel="WARN", mensagem=f"RH {_rotulo(ano, mes)}: {exc}"[:500]))
                db.commit()
                if seguidas >= FALHAS_SEGUIDAS:
                    break
            time.sleep(cfg.pausa_s)
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        return _fim(db, ex, "falha", f"Carga de RH interrompida ({type(exc).__name__}).")
    finally:
        if proprio:
            cliente.close()
    texto = f"{len(novos)} meses carregados ({_rotulo(*novos[0])} a {_rotulo(*novos[-1])})" if novos else "Nenhum mês novo no portal"
    if falhas:
        return _fim(db, ex, "parcial" if novos else "falha", f"{texto}. Com problema: {', '.join(falhas[:6])}.")
    return _fim(db, ex, "sucesso" if novos else "sem_novidades", f"{texto}.")


def estado(db: Session) -> dict:
    def ex_dict(e: Execucao | None) -> dict | None:
        return None if e is None else {"id": e.id, "status": e.status, "mensagem": e.mensagem, "mes_atual": e.tabela_atual,
                                       "lidos": e.lidos or 0, "iniciado_em": e.iniciado_em, "finalizado_em": e.finalizado_em}
    meses = [(r.ano, r.mes, r.linhas) for r in db.scalars(select(MesRh).where(MesRh.linhas > 0))]
    regulares = sorted((a, m) for a, m, _ in meses if m != sv.MES_13)
    return {"meses": len(meses), "linhas": sum(n for *_, n in meses), "primeiro": list(regulares[0]) if regulares else None,
            "ultimo": list(regulares[-1]) if regulares else None, "em_andamento": ex_dict(em_andamento(db)), "ultima_execucao": ex_dict(ultima(db))}
