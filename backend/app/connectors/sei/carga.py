"""Amostra controlada (SEI3) e incremental por andamentos (SEI4) do SEI -> bronze, com hash e contagens.

- Amostra: 1 página de /processos da unidade configurada (limit <= 50) e até 5 detalhes; dos públicos, 1 página de
  documentos (só metadados). Auditoria e log só com contagens e ambiente_origem.
- Incremental (andamentos/completo com dataInicio/dataFim): só com SIGPMF_SRC_SEI_CARGA_LIBERADA=true (validação do
  Leandro), depois de uma amostra com sucesso e dentro da janela noturna. Checkpoint = dataFim; só avança quando a
  execução termina completa (página repetida, teto de páginas ou erro = parcial, checkpoint parado).
- M4 (0023): processo não público vira um marcador mínimo (bronze.sei_processos_nao_publicos: só idProcesso,
  protocolo em dígitos e nível). Andamento de restrito conhecido é descartado (só contagem) sem segurar o checkpoint.
  Pai desconhecido: o processo é consultado (no máximo 3 vezes por pai, contador em controle.sei_pais_pendentes,
  persistente entre execuções). Público -> vai para a bronze e o andamento é gravado; não público -> marcador. Só um
  pai que continua desconhecido segura o checkpoint (parcial); esgotadas as 3 tentativas, alerta
  "pai_desconhecido_esgotado" (nunca descarte silencioso).
- B5: andamento só é gravado se idProcesso E protocolo apontam para o mesmo processo público. B6: protocolo por dígitos.
- Teto de páginas: a janela (dataInicio, dataFim) e o próximo start ficam no checkpoint; a próxima execução continua
  dali (não relê a mesma página para sempre). Só fecha o dia quando a janela termina sem pai desconhecido.
- Reclassificação público -> restrito: as linhas do processo, documentos e andamentos na bronze recebem
  quarentena="sei_reclassificado_restrito" (sem apagar nada), saem das consultas e do explorador, com auditoria.
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
from app.models.camadas import RegistroBronze, SeiProcessoNaoPublico
from app.models.controle import CheckpointCarga, Execucao, Log, SeiPaiPendente

SISTEMA = "SEI"
TIPO_AMOSTRA, TIPO_INCREMENTAL = "amostra_sei", "incremental_sei"
TAB_PROCESSOS, TAB_DOCUMENTOS, TAB_ANDAMENTOS = "processos", "documentos", "andamentos"
LIMITE_PAGINA = 50
MAX_TENTATIVAS_PAI = 3
QUAR_RECLASS = "sei_reclassificado_restrito"
PARAMS_DETALHE = {"sinRetornarInteressados": "S", "sinRetornarAssuntos": "S", "sinRetornarAndamentoGeracao": "S"}
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
                    "interessados_cpf": 0, "interessados_revisao": 0, "reclassificados": 0}
            procs, docs = [], []
            for item in lst[: cfg.detalhes_amostra]:
                prot = pj._t(item.get("protocoloProcedimento"))
                if not prot:
                    continue
                det = cli.get("processo", prot, PARAMS_DETALHE)
                cont["detalhes"] += 1
                if not isinstance(det, dict) or not pj.processo_publico(det):
                    cont["nao_gravados_sigilo"] += 1  # sigiloso/restrito/sem nível: nem grava, nem lê documentos
                    idp = pj._t(det.get("idProcedimento")) if isinstance(det, dict) else None
                    idp = idp or pj._t(item.get("idProcedimento"))
                    if isinstance(det, dict) and idp:
                        cont["reclassificados"] += marca_nao_publico(
                            db, ex, usuario, idp, pj.digitos(prot), pj.nivel_processo(det) or "2", cfg.ambiente)
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
    """Processos do SEI gravados na bronze como públicos (nivel_acesso "0"), fora de quarentena, no mesmo ambiente."""
    por_id: dict[str, str | None] = {}
    por_prot: dict[str, str] = {}
    for d in db.scalars(select(RegistroBronze.dados).where(RegistroBronze.sistema_origem == SISTEMA,
                                                           RegistroBronze.tabela == TAB_PROCESSOS,
                                                           RegistroBronze.quarentena.is_(None))):
        if not isinstance(d, dict) or d.get("nivel_acesso") != pj.PUBLICO or d.get("ambiente_origem") != ambiente:
            continue
        idp, prot = pj._t(d.get("id_procedimento")), pj.digitos(d.get("protocolo"))
        if idp:
            por_id[idp] = prot
        if prot and idp:
            por_prot[prot] = idp
    return pj.Publicos(por_id=por_id, por_protocolo=por_prot)


def processos_nao_publicos(db: Session, ambiente: str) -> pj.NaoPublicos:
    ids, prots = set(), set()
    for m in db.scalars(select(SeiProcessoNaoPublico).where(SeiProcessoNaoPublico.ambiente_origem == ambiente)):
        ids.add(m.id_processo)
        if m.protocolo_digitos:
            prots.add(m.protocolo_digitos)
    return pj.NaoPublicos(ids=frozenset(ids), protocolos=frozenset(prots))


def _do_processo(d: dict, tabela: str, idp: str, prot: str | None) -> bool:
    campos_id = {"processos": "id_procedimento", "documentos": "id_procedimento", "andamentos": "id_processo"}
    campos_prot = {"processos": "protocolo", "documentos": "protocolo_processo", "andamentos": "protocolo"}
    if pj._t(d.get(campos_id[tabela])) == idp:
        return True
    return bool(prot and pj.digitos(d.get(campos_prot[tabela])) == prot)


def marca_nao_publico(db: Session, ex: Execucao, usuario: str, idp: str, prot: str | None, nivel: str,
                      ambiente: str) -> int:
    """Grava/atualiza o marcador mínimo (só id, protocolo em dígitos e nível) e, se o processo estava na bronze como
    público, reclassifica: processo, documentos e andamentos vão para quarentena QUAR_RECLASS (sem DELETE), com
    auditoria só de contagens. Devolve 1 se houve reclassificação, 0 caso contrário."""
    m = db.scalar(select(SeiProcessoNaoPublico).where(SeiProcessoNaoPublico.id_processo == idp,
                                                      SeiProcessoNaoPublico.ambiente_origem == ambiente))
    if m is None:
        m = SeiProcessoNaoPublico(id_processo=idp, ambiente_origem=ambiente, nivel_acesso=nivel)
        db.add(m)
    m.protocolo_digitos, m.nivel_acesso, m.atualizado_em = prot or m.protocolo_digitos, nivel, datetime.now(UTC)
    afetadas = {TAB_PROCESSOS: 0, TAB_DOCUMENTOS: 0, TAB_ANDAMENTOS: 0}
    for r in db.scalars(select(RegistroBronze).where(RegistroBronze.sistema_origem == SISTEMA,
                                                     RegistroBronze.tabela.in_(list(afetadas)),
                                                     RegistroBronze.quarentena.is_(None))):
        d = r.dados if isinstance(r.dados, dict) else {}
        if d.get("ambiente_origem") == ambiente and _do_processo(d, r.tabela, idp, prot):
            r.quarentena = QUAR_RECLASS
            afetadas[r.tabela] += 1
    db.flush()
    if not afetadas[TAB_PROCESSOS]:
        return 0
    audita(db, usuario, "sei_reclassificacao", f"execucao:{ex.id}",
           {"id_processo": idp, "nivel_acesso": nivel, "ambiente_origem": ambiente,
            **{f"{t}_ocultados": n for t, n in afetadas.items()}}, commit=False)
    db.add(Log(execucao_id=ex.id, nivel="WARNING",
               mensagem=f"ambiente_origem={ambiente} reclassificado_para_nao_publico=1 "
                        + " ".join(f"{t}_ocultados={n}" for t, n in afetadas.items())))
    return 1


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


def _pendente(db: Session, chave: str, ambiente: str) -> SeiPaiPendente:
    p = db.scalar(select(SeiPaiPendente).where(SeiPaiPendente.id_processo == chave,
                                               SeiPaiPendente.ambiente_origem == ambiente))
    if p is None:
        p = SeiPaiPendente(id_processo=chave, ambiente_origem=ambiente, tentativas=0, situacao="pendente")
        db.add(p)
        db.flush()
    return p


def _resolve_pais(db: Session, cli: ClienteSei, ex: Execucao, usuario: str, cfg: ConfigSei, itens: list[dict],
                  publicos: pj.Publicos, nao_pub: pj.NaoPublicos) -> tuple[pj.Publicos, pj.NaoPublicos, dict]:
    """Consulta cada pai desconhecido (no máximo MAX_TENTATIVAS_PAI por pai, contador persistente)."""
    c = {"pais_consultados": 0, "pais_publicos": 0, "pais_nao_publicos": 0, "pais_pendentes": 0,
         "pais_esgotados": 0}
    vistos: set[str] = set()
    for a in itens:
        if pj.classifica_andamento(a, publicos, nao_pub) != pj.DESCONHECIDO:
            continue
        idp, prot_txt = pj._t(a.get("idProcesso")), pj._t(a.get("protocoloProcedimento"))
        chave = idp or f"protocolo:{pj.digitos(prot_txt)}"
        if chave in vistos:
            continue
        vistos.add(chave)
        pend = _pendente(db, chave, cfg.ambiente)
        if pend.tentativas >= MAX_TENTATIVAS_PAI:
            c["pais_esgotados"] += 1
            continue
        pend.tentativas += 1
        pend.execucao_id, pend.atualizado_em = ex.id, datetime.now(UTC)
        det = None
        if prot_txt and pj.digitos(prot_txt):
            c["pais_consultados"] += 1
            try:
                det = cli.get("processo", prot_txt, PARAMS_DETALHE)
            except ErroSei:
                det = None
        if not isinstance(det, dict) or not (pj._t(det.get("idProcedimento")) or idp):
            c["pais_esgotados" if pend.tentativas >= MAX_TENTATIVAS_PAI else "pais_pendentes"] += 1
            continue
        id_real = pj._t(det.get("idProcedimento")) or idp
        prot_real = pj.digitos(det.get("procedimentoFormatado")) or pj.digitos(prot_txt)
        if pj.processo_publico(det):
            proc = pj.projeta_processo(det, cfg.ambiente)
            proc["id_procedimento"] = id_real
            proc["protocolo"] = proc["protocolo"] or prot_txt
            _grava(db, TAB_PROCESSOS, "id_procedimento", [proc], ex)
            publicos.por_id[id_real] = prot_real
            if prot_real:
                publicos.por_protocolo[prot_real] = id_real
            pend.situacao = "publico"
            c["pais_publicos"] += 1
        else:
            # Tester (1): do detalhe de processo não público só sai id, protocolo (dígitos) e nível
            marca_nao_publico(db, ex, usuario, id_real, prot_real, pj.nivel_processo(det) or "2", cfg.ambiente)
            nao_pub = pj.NaoPublicos(ids=nao_pub.ids | {id_real} | ({idp} if idp else set()),
                                     protocolos=nao_pub.protocolos | ({prot_real} if prot_real else set()))
            pend.situacao = "restrito"
            c["pais_nao_publicos"] += 1
    db.flush()
    return publicos, nao_pub, c


def executar_incremental(db: Session, usuario: str, agora: datetime | None = None, config: ConfigSei | None = None,
                         transport: httpx.BaseTransport | None = None, dormir=None) -> Execucao:
    cfg = config or ConfigSei.do_ambiente()
    agora = agora or datetime.now(UTC)
    valida_incremental(db, cfg, agora)
    ex = _nova_execucao(db, TIPO_INCREMENTAL, usuario, cfg.ambiente)
    kw = {"dormir": dormir} if dormir else {}
    try:
        ck = _checkpoint(db)
        chave = list(ck.ultima_chave) if ck and ck.ultima_chave else []
        if len(chave) >= 3:  # janela em andamento: mesma janela, continua do próximo start
            ini, fim, start = date.fromisoformat(chave[0]), date.fromisoformat(chave[1]), int(chave[2])
        else:
            fim = agora.date()
            ini = date.fromisoformat(chave[0]) if chave else fim - timedelta(days=1)
            start = 0
        publicos = processos_publicos(db, cfg.ambiente)
        nao_pub = processos_nao_publicos(db, cfg.ambiente)
        with ClienteSei(cfg, transport=transport, **kw) as cli:
            r = paginar(cli, "andamentos", "Andamentos", lambda a: pj._t(a.get("idAndamento")), LIMITE_PAGINA,
                        cfg.max_paginas, params={"dataInicio": _br(ini), "dataFim": _br(fim)}, start=start)
            publicos, nao_pub, cp = _resolve_pais(db, cli, ex, usuario, cfg, r["itens"], publicos, nao_pub)
        classes = [pj.classifica_andamento(a, publicos, nao_pub) for a in r["itens"]]
        regs = [pj.projeta_andamento(a, cfg.ambiente, publicos, nao_pub)
                for a, k in zip(r["itens"], classes, strict=True) if k == pj.PUBLICO_OK]
        restritos = classes.count(pj.RESTRITO)
        inconsistentes = classes.count(pj.INCONSISTENTE)
        desconhecidos = classes.count(pj.DESCONHECIDO)
        b = _grava(db, TAB_ANDAMENTOS, "id_andamento", regs, ex)
        ex.lidos, ex.lotes = len(r["itens"]), r["paginas"]
        ex.novos, ex.atualizados, ex.ignorados = b["novos"], b["atualizados"], b["ignorados"]
        segura = desconhecidos > 0  # só pai ainda desconhecido segura o checkpoint
        continua = r["fim"] == "teto_paginas" and not segura  # janela não terminou: guarda o próximo start
        concluiu = not r["parcial"] and not segura
        avancou = concluiu or continua
        if avancou:
            if ck is None:
                ck = CheckpointCarga(sistema=SISTEMA, tabela=TAB_ANDAMENTOS, posicao=0)
                db.add(ck)
            if concluiu:
                ck.ultima_chave, ck.concluida = [fim.isoformat()], True
            else:
                ck.ultima_chave, ck.concluida = [ini.isoformat(), fim.isoformat(), r["proximo_start"]], False
            ck.execucao_id = ex.id
            ck.posicao = (ck.posicao or 0) + len(regs)
            ck.atualizado_em = datetime.now(UTC)
        descartados = restritos + inconsistentes + desconhecidos
        cont = {"andamentos_lidos": len(regs), "paginas": r["paginas"], "fim": r["fim"], "parcial": r["parcial"],
                "checkpoint_avancou": avancou, "janela_concluida": concluiu,
                "andamentos_nao_gravados_sigilo": descartados, "andamentos_restrito_conhecido": restritos,
                "andamentos_inconsistentes": inconsistentes, "andamentos_pai_desconhecido": desconhecidos, **cp}
        if r["alerta"]:
            cont["alerta"] = r["alerta"]["motivo"]
        if descartados:
            # só contagem (sem id, protocolo ou nome)
            db.add(Log(execucao_id=ex.id, nivel="WARNING",
                       mensagem=f"ambiente_origem={cfg.ambiente} andamentos_descartados={descartados} "
                                f"restrito_conhecido={restritos} inconsistentes={inconsistentes} "
                                f"pai_desconhecido={desconhecidos} checkpoint_avancou={avancou}"))
        if cp["pais_esgotados"]:
            db.add(Log(execucao_id=ex.id, nivel="WARNING",
                       mensagem=f"ambiente_origem={cfg.ambiente} pai_desconhecido_esgotado={cp['pais_esgotados']} "
                                f"tentativas_max={MAX_TENTATIVAS_PAI} checkpoint_avancou=False"))
            cont["alerta"] = "pai_desconhecido_esgotado"
        elif segura:
            cont.setdefault("alerta", "andamentos_descartados")
        parcial = r["parcial"] or segura
        return _fecha(db, ex, "parcial" if parcial else "sucesso", cont, usuario, "sei_incremental")
    except Exception as exc:  # noqa: BLE001
        return _falha(db, ex.id, exc, usuario, "sei_incremental")
