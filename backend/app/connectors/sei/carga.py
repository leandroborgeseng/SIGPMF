"""Amostra controlada (SEI3) e incremental por andamentos (SEI4) do SEI -> bronze, com hash e contagens.

- Amostra: 1 página de /processos da unidade configurada (limit <= 50) e até 5 detalhes; dos públicos, 1 página de
  documentos (só metadados). Auditoria e log só com contagens e ambiente_origem.
- Incremental (andamentos/completo com dataInicio/dataFim): só com SIGPMF_SRC_SEI_CARGA_LIBERADA=true (validação do
  Leandro), depois de uma amostra com sucesso e dentro da janela noturna. Checkpoint = dataFim; só avança quando a
  execução termina completa (página repetida, teto de páginas ou erro = parcial, checkpoint parado).
- Andamento cujo processo não está na bronze como público (restrito, sigiloso ou desconhecido) é descartado; se
  houver descarte, a execução fecha como parcial com alerta e o checkpoint não avança (o dia é relido depois).
"""
from datetime import UTC, date, datetime, timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.bronze.ingestao import ingere
from app.connectors.sei import projecao as pj
from app.connectors.sei.cliente import ClienteSei, ErroSei, SeiDesabilitado
from app.connectors.sei.config import ConfigSei
from app.connectors.sei.paginacao import lista, paginar
from app.core.audit import audita
from app.models.camadas import RegistroBronze
from app.models.controle import CheckpointCarga, Execucao, Log

SISTEMA = "SEI"
TIPO_AMOSTRA, TIPO_INCREMENTAL = "amostra_sei", "incremental_sei"
TAB_PROCESSOS, TAB_DOCUMENTOS, TAB_ANDAMENTOS = "processos", "documentos", "andamentos"
LIMITE_PAGINA = 50
SEM_LIBERACAO = ("Carga do SEI além da amostra só depois da validação do Leandro "
                 "(SIGPMF_SRC_SEI_CARGA_LIBERADA=true).")
SEM_AMOSTRA = "Rode e valide a amostra do SEI antes da carga incremental."
FORA_JANELA = "Carga incremental do SEI só roda na janela noturna ({janela})."


class CargaRecusada(ErroSei):
    pass


def _nova_execucao(db: Session, tipo: str, usuario: str, ambiente: str) -> Execucao:
    ex = Execucao(tipo=tipo, sistema=SISTEMA, status="em_andamento", usuario=usuario, ambiente_origem=ambiente,
                  atualizado_em=datetime.now(UTC))
    db.add(ex)
    db.commit()
    return ex


def _fecha(db: Session, ex: Execucao, status: str, cont: dict, usuario: str, acao: str) -> Execucao:
    ex.status, ex.finalizado_em = status, datetime.now(UTC)
    ex.mensagem = f"ambiente_origem={ex.ambiente_origem} " + " ".join(f"{k}={v}" for k, v in cont.items())
    db.add(Log(execucao_id=ex.id, nivel="INFO" if status != "falha" else "ERROR", mensagem=ex.mensagem))
    audita(db, usuario, acao, f"execucao:{ex.id}",
           {"status": status, "ambiente_origem": ex.ambiente_origem, **{k: v for k, v in cont.items()
                                                                       if isinstance(v, (int, bool)) or v is None}},
           commit=False)
    db.commit()
    return ex


def _falha(db: Session, ex_id: int, exc: Exception, usuario: str, acao: str) -> Execucao:
    db.rollback()
    ex = db.get(Execucao, ex_id)
    msg = str(exc) if isinstance(exc, ErroSei) else type(exc).__name__
    ex.status, ex.finalizado_em = "falha", datetime.now(UTC)
    ex.mensagem = f"ambiente_origem={ex.ambiente_origem} falha: {msg}"
    db.add(Log(execucao_id=ex.id, nivel="ERROR", mensagem=ex.mensagem))
    audita(db, usuario, acao, f"execucao:{ex.id}", {"status": "falha", "ambiente_origem": ex.ambiente_origem},
           commit=False)
    db.commit()
    return ex


def _grava(db: Session, tabela: str, chave: str, regs: list[dict], ex: Execucao) -> dict:
    regs = [r for r in regs if r.get(chave)]
    if not regs:
        return {"novos": 0, "atualizados": 0, "ignorados": 0}
    return ingere(db, SISTEMA, tabela, chave, None, regs, ex.id, confirmar=False)


def executar_amostra(db: Session, usuario: str, config: ConfigSei | None = None,
                     transport: httpx.BaseTransport | None = None, dormir=None) -> Execucao:
    cfg = config or ConfigSei.do_ambiente()
    if not cfg.habilitado:
        raise SeiDesabilitado("Conector SEI inerte: variáveis ausentes (" + ", ".join(cfg.pendencias()) + ").")
    ex = _nova_execucao(db, TIPO_AMOSTRA, usuario, cfg.ambiente)
    kw = {"dormir": dormir} if dormir else {}
    try:
        with ClienteSei(cfg, transport=transport, **kw) as cli:
            limite = min(cfg.limite_amostra, LIMITE_PAGINA)
            lst = lista(cli.get("processos", None, {"start": 0, "limit": limite}), "processos")[:limite]
            cont = {"processos_lidos": len(lst), "detalhes": 0, "processos_gravados": 0, "nao_gravados_sigilo": 0,
                    "documentos_lidos": 0, "documentos_gravados": 0, "documentos_nao_gravados_sigilo": 0,
                    "interessados_cpf": 0, "interessados_revisao": 0}
            procs, docs = [], []
            for item in lst[: cfg.detalhes_amostra]:
                prot = pj._t(item.get("protocoloProcedimento"))
                if not prot:
                    continue
                det = cli.get("processo", prot, {"sinRetornarInteressados": "S", "sinRetornarAssuntos": "S",
                                                 "sinRetornarAndamentoGeracao": "S"})
                cont["detalhes"] += 1
                if not isinstance(det, dict) or not pj.processo_publico(det):
                    cont["nao_gravados_sigilo"] += 1  # sigiloso/restrito/sem nível: nem grava, nem lê documentos
                    continue
                p = pj.projeta_processo(det, cfg.ambiente)
                p["id_procedimento"] = p["id_procedimento"] or pj._t(item.get("idProcedimento"))
                procs.append(p)
                for i in p["interessados"]:
                    cont["interessados_cpf" if i["cidadao_id"] else "interessados_revisao"] += 1
                dl = lista(cli.get("documentos_processo", prot, {"start": 0, "limit": LIMITE_PAGINA}), "documentos")
                cont["documentos_lidos"] += len(dl)
                for d in dl:
                    pd = pj.projeta_documento(d, p["protocolo"], cfg.ambiente)
                    if pd is None:
                        cont["documentos_nao_gravados_sigilo"] += 1
                    else:
                        docs.append(pd)
            bp = _grava(db, TAB_PROCESSOS, "id_procedimento", procs, ex)
            bd = _grava(db, TAB_DOCUMENTOS, "id_documento", docs, ex)
            cont["processos_gravados"], cont["documentos_gravados"] = len(procs), len(docs)
            ex.lidos, ex.lotes = cont["processos_lidos"], 1
            ex.novos = bp["novos"] + bd["novos"]
            ex.atualizados = bp["atualizados"] + bd["atualizados"]
            ex.ignorados = bp["ignorados"] + bd["ignorados"]
            cont["chamadas_get"] = cli.chamadas["get"]
        return _fecha(db, ex, "sucesso" if cont["processos_lidos"] else "sem_novidades", cont, usuario, "sei_amostra")
    except Exception as exc:  # noqa: BLE001
        return _falha(db, ex.id, exc, usuario, "sei_amostra")


def amostra_ok(db: Session) -> bool:
    """Só conta amostra feita no ambiente oficial (homologação não libera carga oficial)."""
    ult = db.scalar(select(Execucao).where(Execucao.tipo == TIPO_AMOSTRA, Execucao.ambiente_origem == "oficial")
                    .order_by(Execucao.id.desc()).limit(1))
    return bool(ult and ult.status == "sucesso" and (ult.lidos or 0) > 0)


def dentro_da_janela(agora: datetime) -> tuple[bool, str]:
    from app.services import carga_gestaoescolar as cge
    return cge.dentro_da_janela(agora)


def processos_publicos(db: Session, ambiente: str) -> pj.Publicos:
    """Processos do SEI gravados na bronze como públicos (nivel_acesso "0") no mesmo ambiente de origem."""
    ids, prots = set(), set()
    for d in db.scalars(select(RegistroBronze.dados).where(RegistroBronze.sistema_origem == SISTEMA,
                                                           RegistroBronze.tabela == TAB_PROCESSOS,
                                                           RegistroBronze.quarentena.is_(None))):
        if not isinstance(d, dict) or d.get("nivel_acesso") != pj.PUBLICO or d.get("ambiente_origem") != ambiente:
            continue
        if pj._t(d.get("id_procedimento")):
            ids.add(pj._t(d.get("id_procedimento")))
        if pj._t(d.get("protocolo")):
            prots.add(pj._t(d.get("protocolo")))
    return pj.Publicos(ids=frozenset(ids), protocolos=frozenset(prots))


def _checkpoint(db: Session) -> CheckpointCarga | None:
    return db.scalar(select(CheckpointCarga).where(CheckpointCarga.sistema == SISTEMA,
                                                   CheckpointCarga.tabela == TAB_ANDAMENTOS))


def _br(d: date) -> str:
    return d.strftime("%d/%m/%Y")


def valida_incremental(db: Session, cfg: ConfigSei, agora: datetime) -> None:
    if not cfg.habilitado:
        raise SeiDesabilitado("Conector SEI inerte: variáveis ausentes (" + ", ".join(cfg.pendencias()) + ").")
    if not cfg.carga_liberada:
        raise CargaRecusada(SEM_LIBERACAO)
    if not amostra_ok(db):
        raise CargaRecusada(SEM_AMOSTRA)
    ok, jan = dentro_da_janela(agora)
    if not ok:
        raise CargaRecusada(FORA_JANELA.format(janela=jan))


def executar_incremental(db: Session, usuario: str, agora: datetime | None = None, config: ConfigSei | None = None,
                         transport: httpx.BaseTransport | None = None, dormir=None) -> Execucao:
    cfg = config or ConfigSei.do_ambiente()
    agora = agora or datetime.now(UTC)
    valida_incremental(db, cfg, agora)
    ex = _nova_execucao(db, TIPO_INCREMENTAL, usuario, cfg.ambiente)
    kw = {"dormir": dormir} if dormir else {}
    try:
        ck = _checkpoint(db)
        fim = agora.date()
        ini = date.fromisoformat(ck.ultima_chave[0]) if ck and ck.ultima_chave else fim - timedelta(days=1)
        with ClienteSei(cfg, transport=transport, **kw) as cli:
            r = paginar(cli, "andamentos", "Andamentos", lambda a: pj._t(a.get("idAndamento")), LIMITE_PAGINA,
                        cfg.max_paginas, params={"dataInicio": _br(ini), "dataFim": _br(fim)})
        publicos = processos_publicos(db, cfg.ambiente)
        proj = [pj.projeta_andamento(a, cfg.ambiente, publicos) for a in r["itens"]]
        regs = [x for x in proj if x is not None]
        descartados = len(proj) - len(regs)
        b = _grava(db, TAB_ANDAMENTOS, "id_andamento", regs, ex)
        ex.lidos, ex.lotes = len(r["itens"]), r["paginas"]
        ex.novos, ex.atualizados, ex.ignorados = b["novos"], b["atualizados"], b["ignorados"]
        parcial = r["parcial"] or descartados > 0
        avancou = not parcial
        if avancou:
            if ck is None:
                ck = CheckpointCarga(sistema=SISTEMA, tabela=TAB_ANDAMENTOS, posicao=0)
                db.add(ck)
            ck.ultima_chave, ck.execucao_id, ck.concluida = [fim.isoformat()], ex.id, True
            ck.posicao = (ck.posicao or 0) + len(regs)
            ck.atualizado_em = datetime.now(UTC)
        cont = {"andamentos_lidos": len(regs), "paginas": r["paginas"], "fim": r["fim"], "parcial": r["parcial"],
                "checkpoint_avancou": avancou,
                "andamentos_nao_gravados_sigilo": descartados}
        if r["alerta"]:
            cont["alerta"] = r["alerta"]["motivo"]
        if descartados:
            # só contagem (sem id, protocolo ou nome): processo restrito/sigiloso/desconhecido na bronze
            db.add(Log(execucao_id=ex.id, nivel="WARNING",
                       mensagem=f"ambiente_origem={cfg.ambiente} andamentos_descartados={descartados} "
                                "motivo=processo_nao_publico_ou_desconhecido checkpoint_avancou=False"))
            cont.setdefault("alerta", "andamentos_descartados")
        return _fecha(db, ex, "parcial" if parcial else "sucesso", cont, usuario, "sei_incremental")
    except Exception as exc:  # noqa: BLE001
        return _falha(db, ex.id, exc, usuario, "sei_incremental")
