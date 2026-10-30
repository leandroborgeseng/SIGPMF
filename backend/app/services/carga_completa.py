"""Carga completa REAL por conector (Saúde e Cemitério). Não usa a carga sintética (essa é só dev/test).

Aceite TL/Tester/Analista (CC1-CC5):
- Liberada só para admin, com a última amostra do conector sem erro e bronze > 0 (motivo visível se não).
- Leitura só pelo guard (um SELECT/WITH por vez, ROLLBACK após cada consulta, timeout da conexão), só com as
  colunas da política (allowlist; nada de CID/senha). Paginação keyset pela chave do mapeamento
  (WHERE chave > última ORDER BY chave, chave composta em ordem lexicográfica, parametrizado), lote limitado (CARGA_COMPLETA_LOTE, teto 5000) e pausa entre lotes
  (CARGA_COMPLETA_PAUSA_MS).
- Cada lote grava bronze + checkpoint numa única transação: reexecutar/retomar não duplica a bronze
  (unique (origem, tabela, id, hash)) e a silver/ouro são reconstruídas a partir da bronze (idempotentes).
- Roda em background; uma carga por conector por vez (advisory lock de sessão, o mesmo da amostra) -> 409.
- Sem leitura do disco (srvdisk) a carga não inicia nem continua (fail-closed, RS3/RS4/RS5).
- Cancelar para no próximo lote sem apagar o que já entrou; disco > RECURSOS_LIMITE_DISCO recusa o início (409) e disco
  sozinha acima de RECURSOS_LIMITE_PAUSA (retomável). Nada é apagado; o checkpoint só volta a 0 quando um ciclo completo termina.
- CC7: etapas Lendo da origem -> Tratando (prata) -> Unificando cidadãos (ouro) -> Concluída, com contador da etapa e
  sinal de vida (atualizado_em). Na prata/ouro o sinal de vida é gravado numa conexão separada (a transação da
  reconstrução fica aberta até o fim). Execução em_andamento sem processo vivo (trava livre) vira "interrompida" e
  pode retomar só prata e ouro a partir da bronze, sem reler a origem.
- Card final: lidos, gravados, bronze, prata, pendências e reconciliação bronze x COUNT(*) da origem.
"""
import logging
import threading
import time
import zlib
from collections import defaultdict
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import func, select, text, update
from sqlalchemy.orm import Session

from app.bronze.ingestao import ambiente_da_origem, ingere
from app.connectors.sqlserver.politicas import politica
from app.core import recursos
from app.core.audit import audita
from app.core.config import get_settings
from app.core.db import SessionLocal, get_engine
from app.gold import visao
from app.ingestion.mapeamentos import MAPEAMENTOS
from app.models.camadas import CorrelacaoPendente, RegistroBronze, Vinculo
from app.models.controle import CheckpointCarga, Execucao, Log
from app.services import carga_conector as cc
from app.services import tabelas_vazias
from app.services.diagnostico import linha_json
from app.silver import processamento

TIPO = "carga_completa_conector"
MODOS = ("completa", "incremental")
MSG_CAMADAS_MANTIDAS = "Nada mudou nas entradas da prata e do ouro: reconstrução pulada (resultado idêntico)."
CONECTORES_COMPLETA = ("saude", "cemiterio", "iptu", "divida", "financeiro", "sil")
NOMES = {"saude": "Saúde", "cemiterio": "Cemitério", "iptu": "IPTU", "divida": "Dívida ativa", "financeiro": "Financeiro",
         "sil": "API SIL"}


SEM_AMOSTRA = "Rode a amostra acima e aprove o resultado para liberar a carga completa."
AMOSTRA_PENDENTE = "Confira o resultado e clique em Aprovar amostra para liberar a carga completa."
# PR-A (CT2/CT3): a amostra disparada a partir desta versão grava aprovacao="manual" na auditoria de início e só
# libera a carga completa depois de "Aprovar amostra". Amostra concluída antes (sem a marca) segue valendo (CT3).
ACAO_INICIO = ("amostra_conector_iniciada", "amostra_sil_iniciada")
ACAO_APROVADA = "amostra_aprovada"
ETAPAS = ("leitura", "prata", "ouro", "concluida")
NOME_ETAPA = {"leitura": "Lendo da origem", "prata": "Tratando (prata)", "ouro": "Unificando cidadãos (ouro)",
              "concluida": "Concluída"}
STATUS_TEXTO = {"em_andamento": "Em andamento · Carga completa", "sucesso": "Concluída · Carga completa",
                "falha": "Falhou · Carga completa", "pausada": "Pausada · Carga completa",
                "cancelada": "Cancelada · Carga completa", "interrompida": "Carga interrompida"}
INTERROMPIDA = ("Carga interrompida: o servidor parou durante a carga. A bronze foi mantida; "
                "retome a prata e o ouro (sem reler a origem) ou inicie a carga de novo.")
log = logging.getLogger("sigpmf.carga_completa")


def sinal_de_vida(ex_id: int, etapa: str, processadas: int | None, total: int | None) -> None:
    """Grava etapa/contador/atualizado_em numa conexão SEPARADA (commit próprio): visível durante a reconstrução,
    cuja transação só confirma no fim."""
    with get_engine().begin() as c:
        c.execute(update(Execucao).where(Execucao.id == ex_id).values(
            etapa=etapa, processadas_etapa=processadas, total_etapa=total, atualizado_em=datetime.now(UTC),
            ultimo_sinal_em=datetime.now(UTC),
            **({"subetapa_contador": processadas, "subetapa_total": total} if etapa == "leitura" else {})))


def _pulso_leitura(ex_id: int):
    """D1: Pulso (sinal de vida a cada 30 s) durante toda a leitura da origem; encerrado no finally do chamador."""
    from app.services import subetapa as sub
    p = sub.Pulso(ex_id)
    p.__enter__()
    return p


def _subetapa_leitura(ex_id: int, tabela: str, lidas: int | None, total: int | None) -> None:
    """CT16: subetapa da leitura em texto (só o nome da tabela) com o contador da tabela atual; nunca derruba a carga."""
    from app.services import subetapa as sub
    try:
        sub.marca(ex_id, "leitura", f"Leitura: lendo {tabela} na origem", lidas, total)
    except Exception:  # noqa: BLE001
        log.warning("subetapa da leitura não gravada (execução %s)", ex_id)


class PrataInconsistente(RuntimeError):
    """CC11: a prata gravada não bate com as chaves distintas esperadas; o ouro não roda."""


def confere_prata(db: Session) -> None:
    """CC11: linhas gravadas na prata == chaves distintas (sem duplicar por join/reprocesso). Só contagens."""
    from app.models.camadas import CorrelacaoPendente as CP
    problemas = []
    for rotulo, m in (("vínculos", Vinculo), ("pendências", CP)):
        linhas = int(db.scalar(select(func.count()).select_from(m)) or 0)
        chaves = int(db.scalar(select(func.count(func.distinct(func.concat(m.sistema, "|", m.tabela, "|", m.chave_origem))))) or 0)
        if linhas != chaves:
            problemas.append(f"{rotulo}: {linhas} linhas para {chaves} chaves distintas")
    if problemas:
        raise PrataInconsistente("prata não bate com as chaves distintas (" + "; ".join(problemas)
                                 + "); o ouro não foi gerado")


class ChaveInvalida(RuntimeError):
    """Tabela cuja chave não serve para keyset (repetida ou com NULL na origem)."""


def problema_chave(tabela: str, ordem_: tuple[str, ...], v: dict) -> str | None:
    """CC9: chave nula vai para a quarentena; a carga só para se os nulos passarem de SIGPMF_CARGA_NULOS_LIMITE_PCT
    (padrão 1%) E de SIGPMF_CARGA_NULOS_LIMITE_MIN linhas (padrão 10). Tabela pequena com poucas nulas não bloqueia,
    mas 100% de nulos (total > 0) sempre bloqueia."""
    chave = ", ".join(ordem_)
    s = get_settings()
    limite, minimo = s.carga_nulos_limite_pct, s.carga_nulos_limite_min
    nulos, total = v["nulos"], v["total"]
    if total is None:  # origem sem contagem (API SIL): a chave é a da própria API, única e nunca nula
        return None
    if nulos:
        pct = 100.0 * nulos / total if total else 100.0
        if total > 0 and nulos >= total:  # ressalva do Tester no #17: tabela inteira com chave nula sempre para
            return (f"Tabela {tabela}: todas as {total} linha(s) têm chave ({chave}) nula na origem (100%); "
                    "nada poderia ir para a bronze, então a carga completa para mesmo abaixo do mínimo de "
                    f"{minimo} linha(s). Corrigir na origem (DBA) ou rever a chave do mapeamento.")
        if pct > limite and nulos > minimo:
            return (f"Tabela {tabela}: {nulos} linha(s) com chave ({chave}) nula na origem ({pct:.2f}% de {total}); "
                    f"acima do limite de {limite:g}% e do mínimo de {minimo} linha(s), a carga completa para. "
                    "Corrigir na origem (DBA) ou ajustar SIGPMF_CARGA_NULOS_LIMITE_PCT/SIGPMF_CARGA_NULOS_LIMITE_MIN.")
    if v["distintas"] != v["total"] - v["nulos"]:
        return (f"Tabela {tabela}: chave ({chave}) não é única na origem ({v['total']} linhas, {v['distintas']} chaves "
                "distintas); a carga completa exige chave única.")
    return None


def verificar_chaves(leitor, conn, sistema: str, pol, detalhe: bool = False) -> dict:
    """Antes de qualquer leitura: COUNT(*) x COUNT(DISTINCT chave) e NULLs, pelo guard. Recusa a tabela inválida."""
    totais, det = {}, {}
    for t in cc.tabelas_do_conector(sistema):
        o = ordem(sistema, t)
        v = leitor.verificar_chave(conn, t, pol, o)
        erro = problema_chave(t, o, v)
        if erro:
            raise ChaveInvalida(erro)
        totais[t], det[t] = v["total"], v
    return det if detalhe else totais


MOTIVO_CHAVE_NULA = "chave_nula"


def _quarentena(db: Session, ex: Execucao, sistema: str, tabela: str, linhas: list[dict], ord_: tuple[str, ...]) -> int:
    """CC9: linhas com chave nula entram na bronze marcadas (quarentena), com os campos nulos; id = hash da linha
    + ordinal da ocorrência (nenhum valor da linha no id; duplicatas idênticas não colapsam). Idempotente: a mesma linha não duplica. Prata e ouro ignoram."""
    from sqlalchemy.dialects.postgresql import insert

    from app.bronze.ingestao import hash_registro
    vals, vistas = {}, defaultdict(int)
    for r in linhas:
        h = hash_registro(r)
        vistas[h] += 1  # linhas idênticas: uma entrada por ocorrência (ordinal), estável entre reexecuções
        k = f"{MOTIVO_CHAVE_NULA}:{h[:32]}:{vistas[h]}"
        vals[k] = {"sistema_origem": sistema, "tabela": tabela, "id_origem": k, "dados": r,
                   "hash": h, "execucao_id": ex.id, "quarentena": MOTIVO_CHAVE_NULA,
                   "quarentena_campos": [c for c in ord_ if r.get(c) is None]}
    if not vals:
        return 0
    db.execute(insert(RegistroBronze).values(list(vals.values())).on_conflict_do_nothing(
        index_elements=["sistema_origem", "tabela", "id_origem", "hash"]))
    db.add(Log(execucao_id=ex.id, nivel="WARN", mensagem=f"{sistema}.{tabela}: {len(linhas)} linha(s) em quarentena "
                                                         f"(chave nula: {sorted({c for v in vals.values() for c in v['quarentena_campos']})})"))
    return len(linhas)


class Bloqueado(RuntimeError):
    """Carga completa não liberada (motivo é seguro para a UI)."""


PAUSADA_NA_AGENDA = "Fonte pausada na agenda. Reative antes de rodar a carga completa."
PAUSADA_NA_AGENDA_AMOSTRA = "Fonte pausada na agenda. Reative antes de rodar a amostra."


def motivo_pausada_agenda(db: Session, conector: str, acao: str = "completa") -> str | None:
    """CT26 v2: item da agenda com ativo=false bloqueia a carga completa e toda rota de amostra (pedir, aprovar,
    rodar). Só lê a agenda: nada é criado e `forcar` não muda. Sem item na agenda, nada muda."""
    from app.models.controle import Agendamento
    ativo = db.scalar(select(Agendamento.ativo).where(Agendamento.conector == conector))
    if ativo is not False:
        return None
    return PAUSADA_NA_AGENDA_AMOSTRA if acao == "amostra" else PAUSADA_NA_AGENDA


def _sistema(conector: str) -> str:
    if conector not in cc.CONECTORES:
        raise cc.ConectorDesconhecido(conector)
    return cc.CONECTORES[conector][0]


def _chave_lock(sistema: str) -> int:
    return zlib.crc32(f"amostra:{sistema}".encode())  # mesma trava da amostra: nunca amostra e completa juntas


def ordem(sistema: str, tabela: str) -> tuple[str, ...]:
    ch = MAPEAMENTOS[(sistema, tabela)]["chave"]
    return tuple(ch) if isinstance(ch, tuple) else (ch,)


def _do_ambiente(sistema: str):
    """A1: checkpoint é por (sistema, tabela, ambiente); só o do ambiente em uso é lido/gravado."""
    return CheckpointCarga.ambiente == ambiente_da_origem(sistema)


def bronze_do_sistema(db: Session, sistema: str) -> int:
    """M1: só linhas do ambiente em uso contam (na oficial: só 'producao'; bronze da homologação não libera)."""
    return int(db.scalar(select(func.count()).select_from(RegistroBronze)
                         .where(RegistroBronze.sistema_origem == sistema,
                                RegistroBronze.ambiente_origem == ambiente_da_origem(sistema))) or 0)


def tem_bronze(db: Session, sistema: str) -> bool:
    """Mesmo critério de bronze_do_sistema(...) > 0, mas com EXISTS (para no 1º registro; não conta milhões)."""
    return bool(db.scalar(select(select(RegistroBronze.id).where(
        RegistroBronze.sistema_origem == sistema,
        RegistroBronze.ambiente_origem == ambiente_da_origem(sistema)).limit(1).exists())))


def _ambiente_da_amostra(db: Session, ex: Execucao) -> str:
    """M1: 'sandbox' se a execução registrou 'ambiente=sandbox' (mensagem/log) ou é uma das amostras da homologação
    já conhecidas (25/31, ver 0020); senão 'producao'."""
    from app.services.marca_sandbox_sil import EXECUCOES
    if ex.id in EXECUCOES or "ambiente=sandbox " in (ex.mensagem or ""):
        return "sandbox"
    n = db.scalar(select(func.count()).select_from(Log).where(
        Log.execucao_id == ex.id, Log.mensagem.contains("ambiente=sandbox ")))
    return "sandbox" if n else "producao"


def _amostra_no_ambiente(db: Session, ex: Execucao) -> bool:
    """M1: amostra da SIL só libera a carga do mesmo ambiente (na oficial: só amostra feita na oficial)."""
    return ex.sistema != "SIL" or _ambiente_da_amostra(db, ex) == ambiente_da_origem("SIL")


# A amostra da SIL disparada pela tela (amostra_sil -> sil/carga.py) grava o tipo "enriquecimento_sil".
# Ela vale como amostra SÓ para o conector "sil" (o filtro por sistema="SIL" também garante isso).
TIPOS_AMOSTRA_EXTRA = {"sil": ("enriquecimento_sil",)}


def motivo_bloqueio(db: Session, conector: str) -> str | None:
    """CC1: None = liberada. Senão, o motivo exibido no botão desabilitado."""
    if conector not in CONECTORES_COMPLETA:
        return "Carga completa ainda não disponível para este conector"
    sistema = _sistema(conector)
    a = ultima_amostra(db, conector)
    if a is None:
        return SEM_AMOSTRA
    if a.status not in ("sucesso", "sem_novidades") or not tem_bronze(db, sistema):
        return SEM_AMOSTRA
    if not amostra_aprovada(db, a):
        return AMOSTRA_PENDENTE
    return None


def ultima_amostra(db: Session, conector: str) -> Execucao | None:
    """Última amostra concluída do conector no ambiente em uso (órfã em andamento/interrompida não conta, CC12)."""
    if conector not in cc.CONECTORES:
        return None
    sistema = cc.CONECTORES[conector][0]
    tipos = cc.TIPOS_AMOSTRA + (TIPOS_AMOSTRA_EXTRA.get(conector, ()))
    q = (select(Execucao).where(Execucao.tipo.in_(tipos), Execucao.sistema == sistema,
                                Execucao.status.not_in(("em_andamento", "interrompida"))).order_by(Execucao.id.desc()))
    return next((e for e in db.scalars(q.limit(50)) if _amostra_no_ambiente(db, e)), None)


def _auditoria(db: Session, acao: str | tuple[str, ...], ex: Execucao):
    from app.models.controle import Auditoria
    acoes = (acao,) if isinstance(acao, str) else acao
    return db.scalars(select(Auditoria).where(Auditoria.acao.in_(acoes), Auditoria.alvo == f"execucao:{ex.id}")
                      .order_by(Auditoria.id.desc()).limit(1)).first()


def exige_aprovacao(db: Session, ex: Execucao) -> bool:
    ini = _auditoria(db, ACAO_INICIO, ex)
    return ini is not None and (ini.detalhe or {}).get("aprovacao") == "manual"


def amostra_aprovada(db: Session, ex: Execucao) -> bool:
    """CT2/CT3: aprovada se teve "Aprovar amostra" ou se é de antes da aprovação manual (registro sem a marca)."""
    if ex.status not in ("sucesso", "sem_novidades"):
        return False
    return not exige_aprovacao(db, ex) or _auditoria(db, ACAO_APROVADA, ex) is not None


def resumo_amostra(db: Session, conector: str) -> dict | None:
    """Resultado da última amostra para o card (UXA3): só contagens e motivo público, sem dado pessoal."""
    a = ultima_amostra(db, conector)
    if a is None:
        return None
    return {"id": a.id, "status": a.status, "finalizado_em": a.finalizado_em, "lidos": a.lidos,
            "gravados": (a.novos or 0) + (a.atualizados or 0), "com_erro": a.ignorados or 0,
            "motivo": (a.erro_publico() or {}).get("motivo") if a.status == "falha" else None,
            "aprovada": amostra_aprovada(db, a)}


class AprovacaoRecusada(RuntimeError):
    pass


def aprovar_amostra(db: Session, usuario: str, conector: str, eid: int, ip: str | None) -> dict:
    """CT2/CT4: só a última amostra concluída com sucesso do conector; auditado com quem, quando (criado_em) e SHA."""
    import os
    a = ultima_amostra(db, conector)
    if a is None or a.id != eid:
        raise AprovacaoRecusada("Só dá para aprovar a última amostra desta fonte. Rode a amostra de novo.")
    if a.status not in ("sucesso", "sem_novidades"):
        raise AprovacaoRecusada("A amostra falhou e não pode ser aprovada. Corrija e rode de novo.")
    if not amostra_aprovada(db, a):
        sha = os.environ.get("RAILWAY_GIT_COMMIT_SHA") or os.environ.get("SOURCE_COMMIT") or os.environ.get("GIT_SHA") or "-"
        audita(db, usuario, ACAO_APROVADA, f"execucao:{a.id}",
               {"conector": conector, "lidos": a.lidos, "com_erro": a.ignorados or 0, "sha": sha}, ip)
    return resumo_amostra(db, conector)


def leitor_padrao(conector: str, db: Session):
    cfg, _ = cc.config_do_conector(conector, db)
    return cc.leitor_de(conector, cfg)


def estimativa(db: Session, conector: str, leitor=None) -> dict:
    """CC2: COUNT(*) por tabela da política (pelo guard), para a confirmação."""
    sistema = _sistema(conector)
    pol = politica(sistema)
    leitor = leitor or leitor_padrao(conector, db)
    conn = leitor.abrir()
    try:
        tabs = verificar_chaves(leitor, conn, sistema, pol)  # ChaveInvalida -> 409 na rota
    finally:
        conn.close()
    return {"tabelas": tabs, "total": sum(v or 0 for v in tabs.values())}


def ativa(db: Session, sistema: str) -> Execucao | None:
    return db.scalar(select(Execucao).where(Execucao.tipo == TIPO, Execucao.sistema == sistema,
                                            Execucao.status == "em_andamento").order_by(Execucao.id.desc()).limit(1))


def disparar(ex_id: int, conector: str, trava, somente_camadas: bool = False) -> None:
    """Background (thread daemon). Os testes substituem por execução síncrona com origem sintética."""
    threading.Thread(target=rodar, args=(ex_id, conector, trava), kwargs={"somente_camadas": somente_camadas},
                     daemon=True, name=f"carga-completa-{conector}").start()


def pode_retomar_camadas(db: Session, sistema: str) -> bool:
    """Última carga interrompida com a leitura da origem já concluída (todos os checkpoints): só falta prata/ouro."""
    ex = db.scalar(select(Execucao).where(Execucao.tipo == TIPO, Execucao.sistema == sistema)
                   .order_by(Execucao.id.desc()).limit(1))
    if ex is None or ex.status != "interrompida":
        return False
    cps = list(db.scalars(select(CheckpointCarga).where(CheckpointCarga.sistema == sistema, _do_ambiente(sistema))))
    tabs = set(cc.tabelas_do_conector(sistema))
    return bool(cps) and tabs <= {c.tabela for c in cps if c.concluida}


def _trava_livre(db: Session, sistema: str) -> bool:
    """True se ninguém segura a trava do conector (nenhum processo vivo rodando a carga)."""
    with db.get_bind().connect() as c:
        livre = bool(c.execute(text("select pg_try_advisory_lock(:k)"), {"k": _chave_lock(sistema)}).scalar())
        if livre:
            c.execute(text("select pg_advisory_unlock(:k)"), {"k": _chave_lock(sistema)})
        c.commit()
    return livre


def _marca_interrompida(db: Session, ex: Execucao) -> None:
    ex.status, ex.mensagem, ex.finalizado_em, ex.tabela_atual = "interrompida", INTERROMPIDA, datetime.now(UTC), None
    db.add(Log(execucao_id=ex.id, nivel="WARN", mensagem=f"{INTERROMPIDA} (etapa: {ex.etapa or 'leitura'})"))


PAUSADA_ORFA = "Carga pausada ({motivo}). Ao continuar, ela segue de onde parou pelos checkpoints."


def marca_orfa(ex: Execucao, motivo: str) -> str:
    """CC12 (TL/Designer): completa sem processo vivo. Leitura incompleta -> 'pausada' (continua pelos checkpoints,
    "Continuar carga completa"); leitura concluída (prata/ouro) -> 'interrompida' ("Retomar prata e ouro").
    Não grava log/auditoria (quem chama grava). Devolve o texto do log."""
    agora = datetime.now(UTC)
    if ex.etapa in ("prata", "ouro"):
        ex.status, ex.mensagem, ex.finalizado_em, ex.tabela_atual = "interrompida", INTERROMPIDA, agora, None
        return f"{INTERROMPIDA} (etapa: {ex.etapa}; {motivo})"
    ex.status, ex.pausa_motivo, ex.finalizado_em, ex.tabela_atual = "pausada", PAUSADA_ORFA.format(motivo=motivo), agora, None
    return f"Carga completa pausada: {motivo}. A bronze e os checkpoints foram mantidos."


def _interrompe_orfa(db: Session, ex: Execucao, usuario: str, motivo: str) -> None:
    """CC12: execução sem processo vivo (auditado): amostra interrompida; completa pausada/interrompida (marca_orfa)."""
    from app.services import trava as tv
    tv.interromper(db, ex, usuario, motivo, automatico=True)


def interromper_orfas(db: Session) -> int:
    """Startup (CC7/CC12): toda amostra ou carga completa em_andamento sem processo vivo (trava livre) vira
    'interrompida', auditado. A bronze e os checkpoints ficam."""
    from app.services import trava as tv
    n = 0
    from app.services.carga_gestaoescolar import TIPO_COMPLETA as TIPO_EDUCACAO  # trava própria (mesma chave)
    tipos = (*tv.TIPOS_CARGA, TIPO_EDUCACAO)
    for ex in list(db.scalars(select(Execucao).where(Execucao.tipo.in_(tipos), Execucao.status == "em_andamento"))):
        if ex.sistema and _trava_livre(db, ex.sistema):
            _interrompe_orfa(db, ex, "sistema", "reinício do servidor")
            n += 1
    db.commit()
    if n:
        log.warning("%d execução(ões) em andamento sem processo vivo marcadas como interrompidas", n)
    return n


def iniciar(db: Session, usuario: str, conector: str, somente_camadas: bool = False, modo: str = "completa") -> Execucao:
    """IN2: modo 'completa' relê a origem inteira (keyset + checkpoint); 'incremental' só o alterado desde a marca d'água."""
    if modo not in MODOS:
        raise ValueError(f"Modo de carga inválido: {modo!r}")
    sistema = _sistema(conector)
    motivo = motivo_bloqueio(db, conector)
    if motivo:
        raise Bloqueado(motivo)
    if somente_camadas and not pode_retomar_camadas(db, sistema):
        raise Bloqueado("Só dá para retomar prata e ouro de uma carga interrompida com a leitura da origem concluída.")
    disco = recursos.motivo_disco_cheio(exige_leitura=True)
    if disco:
        raise Bloqueado(disco)
    # Trava de sessão numa conexão dedicada que acompanha a carga até o fim (passa para o background).
    trava = db.get_bind().connect()
    if not trava.execute(text("select pg_try_advisory_lock(:k)"), {"k": _chave_lock(sistema)}).scalar():
        trava.close()
        from app.services import trava as tv
        raise cc.Conflito(tv.conflito(db, sistema, conector, usuario))
    try:
        from app.services import trava as tv
        for velha in tv.em_andamento(db, sistema):  # temos a trava: ninguém roda; sobrou de um reinício
            # completa -> 'pausada' (retomável do checkpoint, como antes); amostra -> 'interrompida'. Auditado.
            tv.interromper(db, velha, usuario, "sem processo vivo (trava livre)", automatico=True)
        ex = Execucao(tipo=TIPO, sistema=sistema, status="em_andamento", usuario=usuario, cancelar=False,
                      etapa="prata" if somente_camadas else "leitura", atualizado_em=datetime.now(UTC),
                      modo=None if somente_camadas else modo)
        if somente_camadas:  # leitura já concluída: total/contagens vêm da bronze, a origem não é lida
            ex.total_origem = sum(c.total_origem or 0 for c in db.scalars(
                select(CheckpointCarga).where(CheckpointCarga.sistema == sistema, _do_ambiente(sistema))))
        db.add(ex)
        db.commit()
    except Exception:
        _solta(trava, sistema)
        raise
    if somente_camadas:
        disparar(ex.id, conector, trava, somente_camadas=True)
    else:
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
    cps = {c.tabela: c for c in db.scalars(select(CheckpointCarga).where(CheckpointCarga.sistema == sistema, _do_ambiente(sistema)))}
    for t in tabelas:
        if t not in cps:
            cps[t] = CheckpointCarga(sistema=sistema, tabela=t, ambiente=ambiente_da_origem(sistema), posicao=0,
                                     concluida=False)
            db.add(cps[t])
    cps = {t: cps[t] for t in tabelas}
    if all(c.concluida for c in cps.values()):  # ciclo anterior completo: novo ciclo desde o início
        for c in cps.values():
            c.posicao, c.concluida, c.ultima_chave = 0, False, None
    db.flush()
    return cps


STATUS_PARCIAL = "parcial"


def _parcial(conn) -> dict | None:
    """Leitor com teto de lotes (API SIL): {"parcial": True, "lotes_lidos": n, "ha_mais": True} quando parou no teto e a
    origem ainda tem dados; None nos demais leitores e quando a origem acabou."""
    f = getattr(conn, "parcial", None)
    return f() if callable(f) else None


def _finaliza_parcial(db: Session, ex: Execucao, conector: str, parcial: dict, t_leitura: float) -> None:
    """Execução PARCIAL (teto de lotes): não é carga completa. Checkpoint fica aberto (a próxima execução continua do
    último CCM), não há reconciliação, prata/ouro NÃO são reconstruídas e camadas_estado não muda. A agenda não
    marca ultima_completa_em nem a assinatura (status != sucesso)."""
    ex.tabela_atual = None
    _fim_etapa(db, ex, "leitura", t_leitura, {"lidos": ex.lidos, "novos": ex.novos, **parcial})
    ex.etapa, ex.atualizado_em = "concluida", datetime.now(UTC)
    motivo = (f"interrompida: página anômala da API ({parcial['alerta']['motivo']}: "
              f"{parcial['alerta']['ccms_distintos']} CCM distinto(s) em {parcial['alerta']['itens']} itens)"
              if parcial.get("alerta") else "teto SIL_MAX_LOTES_POR_EXECUCAO")
    _finaliza(db, ex, STATUS_PARCIAL,
              f"parcial: {parcial['lotes_lidos']} lote(s) lido(s), há mais na origem de {NOMES[conector]} "
              f"({motivo}). lidos={ex.lidos} novos={ex.novos} atualizados={ex.atualizados}. "
              "Não é carga completa: prata e ouro não foram reconstruídas; a próxima execução continua de onde parou.",
              extra=parcial)


def _finaliza(db: Session, ex: Execucao, status: str, mensagem: str, extra: dict | None = None) -> None:
    ex.status, ex.mensagem, ex.finalizado_em = status, mensagem, datetime.now(UTC)
    nivel = "ERROR" if status == "falha" else "WARN" if status in ("pausada", "cancelada", STATUS_PARCIAL) else "INFO"
    db.add(Log(execucao_id=ex.id, nivel=nivel, mensagem=mensagem))
    db.commit()
    audita(db, ex.usuario, "carga_completa_finalizada", f"execucao:{ex.id}",
           {"resultado": status, "lidas": ex.lidos, "gravadas": ex.novos + ex.atualizados, "ignoradas": ex.ignorados,
            "total_origem": ex.total_origem, **_origem_sil(ex), **(extra or {})})


def _origem_sil(ex: Execucao) -> dict:
    if ex.sistema != "SIL":
        return {}
    from app.connectors.sil.config import ConfigSil
    return ConfigSil.do_ambiente().origem_publica()


def rodar(ex_id: int, conector: str, trava, leitor=None, db: Session | None = None, somente_camadas: bool = False) -> None:
    sistema = _sistema(conector)
    proprio = db is None
    db = db or SessionLocal()
    try:
        ex = db.get(Execucao, ex_id)
        try:
            if somente_camadas:  # retomada: nenhum conector é criado nem lido
                db.add(Log(execucao_id=ex.id, nivel="INFO", mensagem=f"Retomando prata e ouro de {conector} a partir "
                                                                      "da bronze (sem reler a origem)"))
                db.commit()
                _camadas(db, ex, conector, sistema, pode_pular=False)  # pedido explícito: sempre reconstrói
            elif ex.modo == "incremental":
                if conector in cc.CONECTORES_API:
                    db.add(Log(execucao_id=ex.id, nivel="INFO", mensagem=f"Base da API SIL: {cc.base_sil_log()}"))
                    db.commit()
                _executa_incremental(db, ex, conector, sistema, leitor or leitor_padrao(conector, db))
            else:
                if conector in cc.CONECTORES_API:  # TL 29/10: registra a base da SIL usada nesta execução
                    db.add(Log(execucao_id=ex.id, nivel="INFO", mensagem=f"Base da API SIL: {cc.base_sil_log()}"))
                    db.commit()
                _executa(db, ex, conector, sistema, leitor or leitor_padrao(conector, db))
        except Exception as exc:  # noqa: BLE001 - lotes já confirmados ficam; o checkpoint permite retomar
            db.rollback()
            ex = db.get(Execucao, ex_id)
            ex.status = "falha"
            ex.registra_erro(exc)
            ex.tabela_atual = None
            motivo = str(exc) if isinstance(exc, ChaveInvalida | PrataInconsistente) else ex.erro_publico()["motivo"]
            _finaliza(db, ex, "falha", f"Falha na carga completa de {NOMES[conector]}: {motivo}. "
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
    pulso = _pulso_leitura(ex.id)  # D1: sinal a cada 30 s mesmo com a origem lenta
    try:
        det = verificar_chaves(leitor, conn, sistema, pol, detalhe=True)  # repetida/NULL acima do limite: falha
        for t, cp in cps.items():
            cp.total_origem = det[t]["total"]
        ex.total_origem = sum(c.total_origem or 0 for c in cps.values())
        retoma = sum(c.posicao for c in cps.values())
        db.add(Log(execucao_id=ex.id, nivel="INFO", mensagem=(
            f"Carga completa de {conector}: {ex.total_origem} linhas na origem, lote={s.carga_lote}, "
            f"pausa={s.carga_pausa_ms}ms" + (f", retomando do checkpoint ({retoma} já gravadas)" if retoma else ""))))
        db.add(Log(execucao_id=ex.id, nivel="INFO", mensagem=f"Etapa {NOME_ETAPA['leitura']}: início ({ex.total_origem} linhas)"))
        ex.etapa, ex.processadas_etapa, ex.total_etapa, ex.atualizado_em = "leitura", retoma, ex.total_origem, datetime.now(UTC)
        t_leitura = time.monotonic()
        db.commit()
        for t, cp in cps.items():
            if cp.concluida:
                continue
            m = MAPEAMENTOS[(sistema, t)]
            cols = pol.colunas_select(t)
            ord_ = ordem(sistema, t)
            ex.tabela_atual = t
            if cp.posicao == 0 and det[t]["nulos"]:  # CC9: SELECT separado e limitado das chaves nulas
                q = _quarentena(db, ex, sistema, t, [linha_json(r, cols) for r in
                                                    leitor.ler_nulos(conn, t, pol, ord_, det[t]["nulos"])], ord_)
                cp.posicao += q
                ex.lidos = (ex.lidos or 0) + q
            db.commit()
            while True:
                db.refresh(ex)
                if ex.cancelar:
                    ex.tabela_atual = None
                    return _finaliza(db, ex, "cancelada", f"Carga completa de {NOMES[conector]} cancelada. "
                                     "Nada foi apagado; ao iniciar de novo, ela retoma do ponto em que parou.")
                disco = recursos.motivo_disco_cheio(recursos.limite_pausa(), exige_leitura=True)
                if disco:
                    ex.pausa_motivo, ex.tabela_atual = disco, None
                    return _finaliza(db, ex, "pausada", f"Carga completa pausada: {disco}")
                # CC12: sinal de vida a cada lote (conexão separada), antes da leitura que pode demorar na origem
                sinal_de_vida(ex.id, "leitura", sum(c.posicao for c in cps.values()), ex.total_origem)
                _subetapa_leitura(ex.id, t, cp.posicao, cp.total_origem)
                brutas = leitor.ler_pagina(conn, t, pol, ord_, decodifica_chave(cp.ultima_chave), s.carga_lote)
                linhas = [linha_json(r, cols) for r in brutas]
                if brutas:
                    cp.ultima_chave = codifica_chave(_valores_chave(brutas[-1], ord_, cols))
                    c = ingere(db, sistema, t, m["chave"], m["campo_incremental"], linhas, ex.id, s.carga_lote,
                               rowversion=m.get("rowversion"), confirmar=False)
                    for k in ("lidos", "novos", "atualizados", "ignorados", "lotes"):
                        setattr(ex, k, (getattr(ex, k) or 0) + c[k])
                cp.posicao += len(linhas)
                cp.execucao_id = ex.id
                cp.atualizado_em = datetime.now(UTC)
                parcial = _parcial(conn)
                # Teto de lotes (SIL_MAX_LOTES_POR_EXECUCAO) com a origem ainda tendo dados: o checkpoint NÃO fecha.
                cp.concluida = parcial is None and len(linhas) < s.carga_lote
                ex.etapa, ex.total_etapa, ex.atualizado_em = "leitura", ex.total_origem, datetime.now(UTC)
                ex.processadas_etapa = sum(c.posicao for c in cps.values())
                db.commit()  # bronze do lote + checkpoint + progresso: tudo ou nada
                if parcial is not None:
                    return _finaliza_parcial(db, ex, conector, parcial, t_leitura)
                if cp.concluida:
                    break
                if s.carga_pausa_ms:
                    time.sleep(s.carga_pausa_ms / 1000)
        _fim_etapa(db, ex, "leitura", t_leitura, {"lidos": ex.lidos, "novos": ex.novos})
        # Tabela que fechou a carga completa com 0 linhas: WARN + código `tabela_vazia` (status continua sucesso)
        avisa_tabelas_vazias(db, ex, sistema, cps)
        db.commit()
    finally:
        pulso.__exit__(None, None, None)
        conn.close()
    _camadas(db, ex, conector, sistema)


def avisa_tabelas_vazias(db: Session, ex: Execucao, sistema: str, cps: dict) -> list[str]:
    """Fim da leitura da carga COMPLETA (todas as fontes deste motor): tabela concluída com 0 linhas -> WARN e código
    `tabela_vazia` na execução. O status final continua `sucesso`. Devolve as tabelas vazias."""
    vazias = [t for t, c in cps.items() if c.concluida and not (c.posicao or 0)]
    tabelas_vazias.registra(db, ex, sistema, vazias, nivel="WARN")
    return sorted(vazias)


def _inicio_etapa(db: Session, ex: Execucao, etapa: str, total: int | None) -> float:
    ex.etapa, ex.processadas_etapa, ex.total_etapa, ex.atualizado_em = etapa, 0, total, datetime.now(UTC)
    ex.tabela_atual = None
    db.add(Log(execucao_id=ex.id, nivel="INFO", mensagem=f"Etapa {NOME_ETAPA[etapa]}: início"
                                                          + (f" ({total} registros)" if total is not None else "")))
    db.commit()  # nada de ex pendente na transação da reconstrução: o sinal de vida (outra conexão) não espera trava
    log.info("carga completa %s: etapa %s início", ex.id, etapa)
    return time.monotonic()


def _fim_etapa(db: Session, ex: Execucao, etapa: str, t0: float, res: dict) -> None:
    seg = time.monotonic() - t0
    db.add(Log(execucao_id=ex.id, nivel="INFO", mensagem=f"Etapa {NOME_ETAPA[etapa]}: fim em {seg:.1f}s {res}"))
    log.info("carga completa %s: etapa %s fim em %.1fs", ex.id, etapa, seg)


def _log_separado(ex_id: int, etapa: str, t0: float | None, res: dict | None) -> None:
    """Log de início (t0 None) ou fim de etapa numa conexão separada (commit próprio)."""
    if t0 is None:
        msg = f"Etapa {NOME_ETAPA[etapa]}: início"
    else:
        msg = f"Etapa {NOME_ETAPA[etapa]}: fim em {time.monotonic() - t0:.1f}s {res}"
    with get_engine().begin() as c:
        c.execute(Log.__table__.insert().values(execucao_id=ex_id, nivel="INFO", mensagem=msg))
    log.info("carga completa %s: %s", ex_id, msg)


def _camadas(db: Session, ex: Execucao, conector: str, sistema: str, pode_pular: bool = True) -> None:
    """Prata e ouro a partir da bronze (todos os sistemas, como a amostra). Sinal de vida em conexão separada.
    IN1: se nenhuma entrada mudou desde a última reconstrução (camadas_estado), a reconstrução é pulada (o resultado
    seria idêntico); a reconciliação da carga completa roda do mesmo jeito."""
    from app.services import camadas_estado
    assin = camadas_estado.assinatura(db)
    if pode_pular and camadas_estado.inalterada(db, assin):
        ex.etapa, ex.processadas_etapa, ex.total_etapa, ex.atualizado_em = "concluida", 0, 0, datetime.now(UTC)
        db.add(Log(execucao_id=ex.id, nivel="INFO", mensagem=MSG_CAMADAS_MANTIDAS))
        resumo = MSG_CAMADAS_MANTIDAS
    else:
        total = int(db.scalar(select(func.count(func.distinct(func.concat(
            RegistroBronze.sistema_origem, "|", RegistroBronze.tabela, "|", RegistroBronze.id_origem))))
            .where(RegistroBronze.quarentena.is_(None))) or 0)
        t0 = _inicio_etapa(db, ex, "prata", total)  # faz commit: a trava de transação das camadas vem depois
        from app.services import trava as tv
        tv.aguarda_camadas(db, ex.id)  # prata/ouro são globais: espera outra reconstrução (amostra de outro conector)
        # CC8.5: prata e ouro numa única transação; se cair no meio, as duas voltam ao estado anterior (bronze intacta).
        # Etapa, contador e logs de início/fim vão por conexão separada (visíveis durante a transação).
        from app.services import subetapa as sub
        try:
            with sub.Pulso(ex.id):  # CT16/CT17: sinal a cada 30 s mesmo dentro de uma consulta longa
                med = sub.MedicaoComSinal(ex.id)
                sres = processamento.reconstruir(
                    db, progresso=lambda n, tot: (sinal_de_vida(ex.id, "prata", n, tot), med.subcontador(n, tot)),
                    medicao=med)
            confere_prata(db)  # CC11: antes do ouro; se falhar, a transação volta (prata/ouro anteriores ficam)
            _log_separado(ex.id, "prata", t0, sres)
            sinal_de_vida(ex.id, "ouro", 0, sres["vinculos"])
            _log_separado(ex.id, "ouro", None, None)
            t1 = time.monotonic()
            sub.marca(ex.id, "ouro", sub.TEXTO_OURO, 0, sres["vinculos"])
            with sub.Pulso(ex.id):
                gres = visao.reconstruir(db, progresso=lambda n, tot: (sinal_de_vida(ex.id, "ouro", n, tot),
                                                                      sub.sinal(ex.id, n, tot)))
        finally:
            sub.finaliza(ex.id)  # fecha a subetapa aberta mesmo se a prata/ouro falhar
        _log_separado(ex.id, "ouro", t1, gres)
        ex.etapa, ex.processadas_etapa, ex.atualizado_em = "concluida", sres["vinculos"], datetime.now(UTC)
        camadas_estado.registra(db, assin, ex.id)  # mesma transação da prata/ouro (commit no _finaliza)
        resumo = f"silver={sres} gold={gres}"
    lido = f"lidos={ex.lidos} novos={ex.novos} atualizados={ex.atualizados} ignorados={ex.ignorados}"
    if ex.modo == "incremental":  # a reconciliação compara com o COUNT(*) da última completa: não vale aqui
        return _finaliza(db, ex, "sucesso", f"Carga incremental de {NOMES[conector]} concluída: {lido} {resumo}")
    rec = reconciliacao(db, sistema)
    ok = all(r["ok"] for r in rec.values())
    db.add(Log(execucao_id=ex.id, nivel="INFO" if ok else "WARN",
               mensagem="Reconciliação bronze x origem: " + "; ".join(
                   f"{t} origem={r['origem']} bronze={r['bronze']} quarentena={r['quarentena']}" for t, r in rec.items())))
    quar = sum(r["quarentena"] for r in rec.values())
    res_rec = ("ok com pendências" if quar else "ok") if ok else "divergente"
    aviso = tabelas_vazias.alerta(ex)
    _finaliza(db, ex, "sucesso", f"Carga completa de {NOMES[conector]} concluída: {lido} {resumo} "
                                 f"reconciliacao={res_rec}"
                                 + (f"; {quar} linha(s) em quarentena (chave nula)" if quar else "")
                                 + (f". {aviso['texto']}" if aviso else ""),
              {"alerta": aviso["codigo"], "tabelas_vazias": aviso["tabelas"]} if aviso else None)


def _valores_chave(r, ord_: tuple[str, ...], cols: tuple[str, ...]) -> tuple:
    if isinstance(r, dict):
        return tuple(r.get(c) for c in ord_)
    return tuple(r[cols.index(c)] for c in ord_)


def codifica_chave(valores: tuple) -> list:
    """Valor da chave -> JSON com o tipo preservado (datetime/date/Decimal/bytes) para reenviar como parâmetro."""
    out = []
    for v in valores:
        if isinstance(v, datetime):
            out.append({"dt": v.isoformat()})
        elif isinstance(v, date):
            out.append({"d": v.isoformat()})
        elif isinstance(v, Decimal):
            out.append({"dec": str(v)})
        elif isinstance(v, (bytes, bytearray)):
            out.append({"hex": bytes(v).hex()})
        else:
            out.append(v)
    return out


def decodifica_chave(j: list | None) -> tuple | None:
    if j is None:
        return None
    out = []
    for v in j:
        if isinstance(v, dict) and len(v) == 1:
            (k, x), = v.items()
            v = {"dt": datetime.fromisoformat, "d": date.fromisoformat, "dec": Decimal, "hex": bytes.fromhex}[k](x)
        out.append(v)
    return tuple(out)


def reconciliacao(db: Session, sistema: str) -> dict[str, dict]:
    """Bronze (chaves distintas por tabela) x COUNT(*) da origem lido no início da carga: ok só se forem iguais
    (bronze acima = registro que saiu da origem ou chave antiga; bronze abaixo = faltou linha).
    `ocultas` (#35): quantas das linhas em quarentena são SEI reclassificado para restrito (só a contagem, sem id
    nem valor); o total bruto continua igual para bater com a origem."""
    from app.explorador.servico import QUAR_OCULTA
    out = {}
    for cp in db.scalars(select(CheckpointCarga).where(CheckpointCarga.sistema == sistema, _do_ambiente(sistema)).order_by(CheckpointCarga.id)):
        def conta(quar: bool) -> int:
            return int(db.scalar(select(func.count(func.distinct(RegistroBronze.id_origem))).where(
                RegistroBronze.sistema_origem == sistema, RegistroBronze.tabela == cp.tabela,
                RegistroBronze.quarentena.is_not(None) if quar else RegistroBronze.quarentena.is_(None))) or 0)
        b, q = conta(False), conta(True)
        oc = int(db.scalar(select(func.count(func.distinct(RegistroBronze.id_origem))).where(
            RegistroBronze.sistema_origem == sistema, RegistroBronze.tabela == cp.tabela,
            RegistroBronze.quarentena == QUAR_OCULTA)) or 0)
        if cp.total_origem is None:  # origem sem contagem (API SIL): não há com o que comparar
            out[cp.tabela] = {"origem": None, "bronze": b, "quarentena": q, "ocultas": oc, "ok": True,
                              "status": "sem_contagem"}
            continue
        ok = b + q == cp.total_origem  # CC9: bronze + quarentena == origem, exato
        out[cp.tabela] = {"origem": cp.total_origem, "bronze": b, "quarentena": q, "ocultas": oc, "ok": ok,
                          "status": ("ok_com_pendencias" if q else "ok") if ok else "divergente"}
    return out


def quarentena(db: Session, conector: str) -> list[dict]:
    """CC9.2: detalhe só para admin, sem valores da linha (nem CPF): tabela, motivo, campos nulos, id (hash), data."""
    sistema = _sistema(conector)
    q = select(RegistroBronze.id, RegistroBronze.tabela, RegistroBronze.quarentena, RegistroBronze.quarentena_campos,
               RegistroBronze.id_origem, RegistroBronze.extraido_em).where(
        RegistroBronze.sistema_origem == sistema, RegistroBronze.quarentena.is_not(None)).order_by(RegistroBronze.id)
    return [{"id": r.id, "tabela": r.tabela, "motivo": "Chave nula na origem" if r.quarentena == MOTIVO_CHAVE_NULA
             else r.quarentena, "campos_nulos": r.quarentena_campos or [], "referencia": r.id_origem,
             "extraido_em": r.extraido_em} for r in db.execute(q.limit(1000))]


def situacao(db: Session, conector: str) -> dict:
    """Estado para a UI: liberação, última carga completa (progresso ou card final) e contagens das camadas."""
    sistema = _sistema(conector)
    ex = db.scalar(select(Execucao).where(Execucao.tipo == TIPO, Execucao.sistema == sistema)
                   .order_by(Execucao.id.desc()).limit(1))
    cps = list(db.scalars(select(CheckpointCarga).where(CheckpointCarga.sistema == sistema, _do_ambiente(sistema))))
    motivo = motivo_bloqueio(db, conector) if conector in cc.CONECTORES else None
    if motivo is None and (ex is None or ex.status != "em_andamento"):
        motivo = motivo_pausada_agenda(db, conector)  # CT26: card mostra o motivo e desabilita o botão
    if motivo is None and (ex is None or ex.status != "em_andamento"):
        motivo = recursos.motivo_disco_cheio(exige_leitura=True)
    execucao, sem_progresso = None, False
    if ex is not None and ex.status == "em_andamento":
        visto = ex.atualizado_em or ex.iniciado_em
        if visto is not None and visto.tzinfo is None:
            visto = visto.replace(tzinfo=UTC)
        sem_progresso = visto is not None and (datetime.now(UTC) - visto).total_seconds() > 60 * get_settings().carga_alerta_min
        if sem_progresso and _trava_livre(db, sistema):  # nenhum processo segura a trava: a thread morreu
            _interrompe_orfa(db, ex, "sistema", "thread da carga parou")
            db.commit()
            sem_progresso = False
    if ex is not None:
        execucao = {
            "id": ex.id, "status": ex.status, "usuario": ex.usuario, "iniciado_em": ex.iniciado_em,
            "finalizado_em": ex.finalizado_em, "lidos": ex.lidos, "novos": ex.novos, "atualizados": ex.atualizados,
            "ignorados": ex.ignorados, "lotes": ex.lotes, "gravados": ex.novos + ex.atualizados,
            "total_origem": ex.total_origem, "processados": sum(c.posicao for c in cps),
            # CC12 (Designer): "X de N tabelas já lidas" no card da carga pausada
            "tabelas_lidas": sum(1 for c in cps if c.concluida), "tabelas_total": len(cc.tabelas_do_conector(sistema)), "tabela_atual": ex.tabela_atual,
            "pausa_motivo": ex.pausa_motivo, "cancelamento_pedido": bool(ex.cancelar), "mensagem": ex.mensagem,
            "erro": ex.erro_publico(), "alerta": ex.alerta_publico(), "status_texto": STATUS_TEXTO.get(ex.status, ex.status),
            "etapa": ex.etapa, "etapa_nome": NOME_ETAPA.get(ex.etapa or ""), "processadas_etapa": ex.processadas_etapa,
            "total_etapa": ex.total_etapa, "atualizado_em": ex.atualizado_em, "sem_progresso": sem_progresso,
            "alerta_min": get_settings().carga_alerta_min, "pode_retomar_camadas": pode_retomar_camadas(db, sistema)}
    from app.services import contagens_cache
    camadas, atualizando = contagens_cache.obter(db, f"completa:{sistema}", lambda s: contagens_card(s, sistema))
    sem_retrato = camadas is None
    # cache frio: null (a tela mostra "—"), nunca 0 — zero parece perda de dados (TL #56)
    camadas = camadas or {"camadas": {"bronze": None, "prata": None, "pendencias": None, "gerados_prata": None},
                          "quarentena": None}
    return {
        "conector": conector, "liberada": motivo is None, "motivo": motivo, "execucao": execucao,
        "camadas": camadas["camadas"], "contagens_atualizando": atualizando or sem_retrato,
        "reconciliacao": reconciliacao(db, sistema) if ex is not None and ex.status == "sucesso" else None,
        "quarentena": camadas["quarentena"],
    }


def contagens_card(db: Session, sistema: str) -> dict:
    """Contagens pesadas do card (só SELECT no Postgres), lidas pelo contagens_cache."""
    return {
        "camadas": {
            "bronze": bronze_do_sistema(db, sistema),
            "prata": int(db.scalar(select(func.count()).select_from(Vinculo).where(Vinculo.sistema == sistema)) or 0),
            "pendencias": int(db.scalar(select(func.count()).select_from(CorrelacaoPendente).where(
                CorrelacaoPendente.sistema == sistema, CorrelacaoPendente.status == "pendente")) or 0),
            # CC11: "Registros gerados na prata" (vínculos + pendências), linha separada do contador da etapa
            "gerados_prata": int(db.scalar(select(func.count()).select_from(Vinculo).where(Vinculo.sistema == sistema))
                                 or 0) + int(db.scalar(select(func.count()).select_from(CorrelacaoPendente).where(
                                     CorrelacaoPendente.sistema == sistema)) or 0)},
        "quarentena": int(db.scalar(select(func.count()).select_from(RegistroBronze).where(
            RegistroBronze.sistema_origem == sistema, RegistroBronze.quarentena.is_not(None))) or 0),
    }


def _rotulo(ex: Execucao) -> str:
    return "incremental" if ex.modo == "incremental" else "completa"


def marca_dagua(db: Session, sistema: str, tabela: str):
    """IN2: maior data de alteração já gravada na bronze para a tabela, menos a margem (CARGA_INCREMENTAL_MARGEM_H).
    Devolve datetime ingênuo (como a origem guarda) ou None (sem histórico: a tabela é lida por inteiro)."""
    from datetime import timedelta
    maior = db.scalar(select(func.max(RegistroBronze.atualizado_origem_em)).where(
        RegistroBronze.sistema_origem == sistema, RegistroBronze.tabela == tabela, RegistroBronze.quarentena.is_(None)))
    if maior is None:
        return None
    return maior.replace(tzinfo=None) - timedelta(hours=get_settings().carga_incremental_margem_h)


def _executa_incremental(db: Session, ex: Execucao, conector: str, sistema: str, leitor) -> None:
    """IN2: lê só as linhas com campo incremental >= marca d'água (keyset em memória, sem checkpoint nem COUNT
    DISTINCT da chave: a unicidade já foi verificada na última carga completa). Tabela sem campo incremental na
    política, ou sem histórico na bronze, é lida por inteiro. Exclusões na origem ficam para a completa periódica."""
    s = get_settings()
    pol = politica(sistema)
    tabelas = cc.tabelas_do_conector(sistema)
    cc._garante_catalogo(db, sistema, tabelas)
    ex.etapa, ex.processadas_etapa, ex.total_etapa, ex.total_origem = "leitura", 0, None, None
    ex.atualizado_em = datetime.now(UTC)
    db.commit()
    t_leitura = time.monotonic()
    conn = leitor.abrir()
    pulso = _pulso_leitura(ex.id)  # D1: sinal a cada 30 s mesmo com a origem lenta
    try:
        for t in tabelas:
            m = MAPEAMENTOS[(sistema, t)]
            cols = pol.colunas_select(t)
            ord_ = ordem(sistema, t)
            campo = m.get("campo_incremental")
            marca = marca_dagua(db, sistema, t) if campo and campo in cols else None
            desde = (campo, marca) if marca is not None else None
            db.add(Log(execucao_id=ex.id, nivel="INFO", mensagem=(
                f"{sistema}.{t}: incremental desde {marca:%Y-%m-%d %H:%M} ({campo})" if desde
                else f"{sistema}.{t}: sem campo incremental ou sem histórico na bronze, leitura completa da tabela")))
            ex.tabela_atual = t
            db.commit()
            ultima = None
            while True:
                db.refresh(ex)
                if ex.cancelar:
                    ex.tabela_atual = None
                    return _finaliza(db, ex, "cancelada", f"Carga incremental de {NOMES[conector]} cancelada. "
                                                          "O que já entrou foi mantido.")
                disco = recursos.motivo_disco_cheio(recursos.limite_pausa(), exige_leitura=True)
                if disco:
                    ex.pausa_motivo, ex.tabela_atual = disco, None
                    return _finaliza(db, ex, "pausada", f"Carga incremental pausada: {disco}")
                sinal_de_vida(ex.id, "leitura", ex.processadas_etapa, None)
                _subetapa_leitura(ex.id, t, ex.processadas_etapa, None)
                brutas = (leitor.ler_pagina(conn, t, pol, ord_, ultima, s.carga_lote, desde=desde) if desde
                          else leitor.ler_pagina(conn, t, pol, ord_, ultima, s.carga_lote))
                linhas = [linha_json(r, cols) for r in brutas]
                if brutas:
                    ultima = _valores_chave(brutas[-1], ord_, cols)
                    c = ingere(db, sistema, t, m["chave"], campo, linhas, ex.id, s.carga_lote,
                               rowversion=m.get("rowversion"), confirmar=False)
                    for k in ("lidos", "novos", "atualizados", "ignorados", "lotes"):
                        setattr(ex, k, (getattr(ex, k) or 0) + c[k])
                ex.processadas_etapa = (ex.processadas_etapa or 0) + len(linhas)
                ex.atualizado_em = datetime.now(UTC)
                db.commit()
                parcial = _parcial(conn)
                if parcial is not None:
                    ex.tabela_atual = None
                    return _finaliza_parcial(db, ex, conector, parcial, t_leitura)
                if len(linhas) < s.carga_lote:
                    break
                if s.carga_pausa_ms:
                    time.sleep(s.carga_pausa_ms / 1000)
        ex.tabela_atual = None
        _fim_etapa(db, ex, "leitura", t_leitura, {"lidos": ex.lidos, "novos": ex.novos, "modo": "incremental"})
    finally:
        pulso.__exit__(None, None, None)
        conn.close()
    _camadas(db, ex, conector, sistema)
