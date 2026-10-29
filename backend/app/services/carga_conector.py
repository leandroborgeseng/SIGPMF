"""Carga real por conector (PB12/PB13): amostra via política de tabelas, transacional e auditada.

- Banco de origem por conector: SQLSERVER_DATABASE_<CONECTOR>; sem ele, SQLSERVER_DATABASE (fallback registrado
  no controle). Quando o catálogo do #7 (controle.bases_catalogo) entrar, o nome do banco vem de lá.
- Lê TODAS as tabelas antes de gravar: falha na origem (timeout, erro) não deixa carga parcial na bronze.
- Bronze + silver + gold numa única transação; erro -> ROLLBACK e controle registra status, erro e fim.
- Uma amostra por conector por vez: 409 se já houver uma em andamento (advisory lock + status).
- A rota responde 202 com o id da execução e a amostra roda em background (TL 27/10: o proxy cortava o POST
  quando prata/ouro passavam do timeout). Status/etapa/sinal de vida em GET .../amostra/{id}.
- Nenhum host, usuário ou senha em mensagem, log ou resposta.
"""
import threading
from datetime import UTC, datetime

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.bronze.ingestao import ingere
from app.connectors.sqlserver.connector import ConfigSqlServer, SqlServerConnector
from app.connectors.sqlserver.politicas import politica
from app.core.config import get_settings
from app.core.db import SessionLocal
from app.gold import visao
from app.ingestion.mapeamentos import MAPEAMENTOS, NOMES_SISTEMAS
from app.models.controle import BaseCatalogo, Execucao, Log, Sistema, TabelaCatalogo
from app.services.diagnostico import MSG_ORIGEM_VAZIA, ORIGEM_VAZIA, FalhaLeitura, linha_json
from app.silver import processamento

# id do conector na API -> sistema da política / mapeamento e banco de origem esperado (PB12)
CONECTORES = {"saude": ("SAUDE", "Saude"), "cemiterio": ("CEMITERIO", "Cemiterio"), "iss": ("ISS", "ISS"),
              "iptu": ("IPTU", "IPTU"), "divida": ("DIVIDA", "Divida"), "financeiro": ("FINANCEIRO", "Financeiro"),
              "sil": ("SIL", "API SIL")}
CONECTORES_API = ("sil",)  # origem REST: leitor próprio, sem banco SQL Server


def leitor_de(conector: str, cfg):
    """Leitor da origem do conector: API SIL ou SQL Server (mesma interface)."""
    if conector == "sil":
        from app.connectors.sil.conector import SilConnector
        return SilConnector(nome=conector)
    return SqlServerConnector(cfg, nome=conector)
TIPO_AMOSTRA = "amostra_conector"
TIPO_AMOSTRA_ISS = "carga_municipe_amostra"  # ISS usa a carga do Munícipe (#6) com a política ISS
TIPOS_AMOSTRA = (TIPO_AMOSTRA, TIPO_AMOSTRA_ISS)


class ConectorDesconhecido(LookupError):
    pass


class Conflito(RuntimeError):
    pass


class SemMapeamento(RuntimeError):
    pass


def tabelas_do_conector(sistema: str) -> list[str]:
    if sistema == "ISS":
        return ["Municipes"]  # política ISS do #6; classificação própria (app.ingestion.municipe)
    pol = politica(sistema)
    if pol is None:
        return []
    return [t for t in pol.colunas if (sistema, t) in MAPEAMENTOS]


def banco_do_catalogo(db: Session | None, conector: str) -> str | None:
    """PB12: nome do banco de origem da base do catálogo (controle.bases_catalogo.nome) ligada ao conector."""
    if db is None:
        return None
    b = db.scalar(select(BaseCatalogo).where(BaseCatalogo.conjunto == conector, BaseCatalogo.status == "implementado"))
    return b.nome if b else None


def config_do_conector(conector: str, db: Session | None = None) -> tuple[ConfigSqlServer, bool]:
    """Ordem: SQLSERVER_DATABASE_<CONECTOR> (override explícito) > catálogo (PB12) > SQLSERVER_DATABASE (fallback)."""
    cfg, fallback = ConfigSqlServer.para_conector(get_settings(), conector)
    if fallback:
        nome = banco_do_catalogo(db, conector)
        if nome:
            cfg.database = nome
            return cfg, False
    return cfg, fallback


def _garante_catalogo(db: Session, sistema: str, tabelas: list[str]) -> None:
    sis = db.scalar(select(Sistema).where(Sistema.codigo == sistema))
    if sis is None:
        m0 = MAPEAMENTOS[(sistema, tabelas[0])]
        sis = Sistema(codigo=sistema, nome=NOMES_SISTEMAS.get(sistema, sistema), dominio=m0["dominio"],
                      descricao="Conector SQL Server (somente leitura)")
        db.add(sis)
        db.flush()
    for t in tabelas:
        if db.scalar(select(TabelaCatalogo).where(TabelaCatalogo.sistema_id == sis.id, TabelaCatalogo.nome == t)) is None:
            m = MAPEAMENTOS[(sistema, t)]
            ch = m["chave"]
            db.add(TabelaCatalogo(sistema_id=sis.id, nome=t, chave=",".join(ch) if isinstance(ch, tuple) else ch,
                                  campo_incremental=m["campo_incremental"]))


def amostra_reconciliada(db: Session, sistema: str) -> Execucao | None:
    """Última amostra com sucesso cujas contagens fecham (lidos = novos + atualizados + ignorados)."""
    for e in db.scalars(select(Execucao).where(Execucao.tipo.in_(TIPOS_AMOSTRA), Execucao.sistema == sistema,
                                               Execucao.status.in_(("sucesso", "sem_novidades")))
                        .order_by(Execucao.id.desc())):
        if e.lidos > 0 and e.lidos == e.novos + e.atualizados + e.ignorados + (e.duplicados or 0) + (e.pendentes or 0):
            return e
    return None


def _valida(conector: str) -> str:
    if conector not in CONECTORES:
        raise ConectorDesconhecido(conector)
    sistema = CONECTORES[conector][0]
    if not tabelas_do_conector(sistema):
        raise SemMapeamento(f"Conector {conector} ainda sem mapeamento para a silver nesta versão.")
    return sistema


def _pega_trava(db: Session, sistema: str, conector: str, usuario: str):
    """Trava de sessão (#20) numa conexão dedicada: não pode depender da conexão que a Session troca a cada commit."""
    from app.services import trava as tv
    trava = db.get_bind().connect()
    if not trava.execute(text("select pg_try_advisory_lock(:k)"), {"k": tv.chave_lock(sistema)}).scalar():
        trava.close()
        raise Conflito(tv.conflito(db, sistema, conector, usuario))
    return trava


def _solta_trava(trava, sistema: str) -> None:
    from app.services import trava as tv
    try:
        trava.execute(text("select pg_advisory_unlock(:k)"), {"k": tv.chave_lock(sistema)})
        trava.commit()
    finally:
        trava.close()


def iniciar_amostra(db: Session, usuario: str, conector: str) -> Execucao:
    """Rota POST .../amostra (TL 27/10): responde rápido (202) com a execução e roda em background, como a carga
    completa. Validação, trava (#20) e limpeza de órfãs acontecem aqui, ANTES do 202: 404/409 continuam síncronos.
    A trava passa para a thread e só é solta quando a amostra termina (sucesso, falha ou exceção)."""
    sistema = _valida(conector)
    from app.services import trava as tv
    trava = _pega_trava(db, sistema, conector, usuario)
    try:
        tv.limpa_orfas(db, sistema, usuario)  # temos a trava: em_andamento deste sistema sobrou de um reinício
        ex = Execucao(tipo=TIPO_AMOSTRA_ISS if sistema == "ISS" else TIPO_AMOSTRA, sistema=sistema,
                      status="em_andamento", usuario=usuario, etapa="leitura", atualizado_em=datetime.now(UTC))
        db.add(ex)
        db.commit()
    except Exception:
        _solta_trava(trava, sistema)
        raise
    disparar_amostra(ex.id, conector, usuario, trava)
    return ex


def disparar_amostra(ex_id: int, conector: str, usuario: str, trava) -> None:
    """Background (thread daemon), como carga_completa.disparar. Os testes trocam por execução síncrona."""
    threading.Thread(target=rodar_amostra, args=(ex_id, conector, usuario, trava), daemon=True,
                     name=f"amostra-{conector}-{ex_id}").start()


def rodar_amostra(ex_id: int, conector: str, usuario: str, trava) -> None:
    """Corpo da thread: sessão própria; SEMPRE solta a trava e nunca deixa a execução em_andamento."""
    sistema = CONECTORES[conector][0]
    db = SessionLocal()
    try:
        executar_amostra(db, usuario, conector, execucao_id=ex_id, trava=trava)
        db.expire_all()
        ex = db.get(Execucao, ex_id)
        from app.core.audit import audita
        audita(db, usuario, "amostra_conector_executada", f"execucao:{ex_id}", {"status": ex.status, "lidos": ex.lidos})
    except Exception as exc:  # noqa: BLE001 - rede de segurança: executar_amostra já trata as falhas esperadas
        db.rollback()
        ex = db.get(Execucao, ex_id)
        if ex is not None and ex.status == "em_andamento":
            ex.status = "falha"
            ex.registra_erro(exc)
            ex.mensagem = f"Falha na amostra de {conector}: {ex.erro_publico()['motivo']}"
            ex.finalizado_em = datetime.now(UTC)
            db.add(Log(execucao_id=ex.id, nivel="ERROR", mensagem=ex.mensagem))
            db.commit()
    finally:
        db.close()
        _solta_trava(trava, sistema)


def executar_amostra(db: Session, usuario: str, conector: str, leitor=None, execucao_id: int | None = None,
                     trava=None) -> Execucao:
    """Amostra síncrona (corpo do background; testes chamam direto).
    `leitor`: objeto com ler_amostra(tabela, politica=...) (testes injetam origem sintética).
    `execucao_id`/`trava`: execução e trava já criadas por iniciar_amostra (quem passou a trava a solta)."""
    sistema = _valida(conector)
    tabelas = tabelas_do_conector(sistema)
    pol = politica(sistema)
    from app.services import trava as tv
    propria = trava is None
    if propria:
        trava = _pega_trava(db, sistema, conector, usuario)
    try:
        if propria:
            # CC12: temos a trava, então nenhuma execução "em_andamento" deste sistema está viva (sobrou de reinício)
            tv.limpa_orfas(db, sistema, usuario)
        ex = db.get(Execucao, execucao_id) if execucao_id is not None else None
        cfg, fallback = config_do_conector(conector, db)
        especifico = getattr(get_settings(), f"sqlserver_database_{conector}", None)
        origem_banco = ("SQLSERVER_DATABASE (fallback)" if fallback else f"SQLSERVER_DATABASE_{conector.upper()}"
                        if especifico else "catálogo (controle.bases_catalogo)")
        if sistema == "ISS":
            return _amostra_iss(db, usuario, cfg, fallback, origem_banco, leitor, ex)
        if ex is None:
            ex = Execucao(tipo=TIPO_AMOSTRA, sistema=sistema, status="em_andamento", usuario=usuario, etapa="leitura",
                          atualizado_em=datetime.now(UTC))
            db.add(ex)
            db.commit()
        db.add(Log(execucao_id=ex.id, nivel="INFO", mensagem="Origem do conector sil: API SIL (REST, só leitura)")
               if conector in CONECTORES_API else
               Log(execucao_id=ex.id, nivel="WARN" if fallback else "INFO",
                   mensagem=f"Banco de origem do conector {conector}: {cfg.database or '-'} via {origem_banco}"))
        db.commit()
        leitor = leitor or leitor_de(conector, cfg)
        tot = {"lidos": 0, "novos": 0, "atualizados": 0, "ignorados": 0, "lotes": 0}
        try:
            lidas = {}
            for t in tabelas:  # tudo lido antes de gravar
                try:
                    cols = pol.colunas_select(t)
                    lidas[t] = [linha_json(r, cols) for r in leitor.ler_amostra(t, politica=pol)]
                    tv.sinal_de_vida(ex.id)
                except Exception as exc:  # noqa: BLE001
                    raise FalhaLeitura(t, exc) from exc
            if not any(lidas.values()):  # 0 linhas: nunca "sem_novidades"
                ex.status, ex.mensagem = ORIGEM_VAZIA, MSG_ORIGEM_VAZIA
                ex.finalizado_em = datetime.now(UTC)
                db.add(Log(execucao_id=ex.id, nivel="WARN", mensagem=MSG_ORIGEM_VAZIA))
                db.commit()
                return ex
            _garante_catalogo(db, sistema, tabelas)
            for t, linhas in lidas.items():
                m = MAPEAMENTOS[(sistema, t)]
                try:
                    c = ingere(db, sistema, t, m["chave"], m["campo_incremental"], linhas, ex.id,
                               get_settings().lote_tamanho, rowversion=m.get("rowversion"), confirmar=False)
                except Exception as exc:  # noqa: BLE001
                    raise FalhaLeitura(t, exc) from exc
                for k in tot:
                    tot[k] += c[k]
                tv.sinal_de_vida(ex.id)
            tv.aguarda_camadas(db, ex.id)  # prata/ouro são globais: uma reconstrução por vez (trava de transação)
            tv.sinal_de_vida(ex.id, "prata", 0, None)
            sres = processamento.reconstruir(db, progresso=lambda n, t: tv.sinal_de_vida(ex.id, "prata", n, t))
            tv.sinal_de_vida(ex.id, "ouro", 0, sres["vinculos"])
            gres = visao.reconstruir(db, progresso=lambda n, t: tv.sinal_de_vida(ex.id, "ouro", n, t))
            for k, v in tot.items():
                setattr(ex, k, v)
            ex.status = "sucesso" if (tot["novos"] or tot["atualizados"]) else "sem_novidades"
            ex.etapa = "concluida"
            ex.mensagem = (f"banco={'fallback' if fallback else 'conector'} lidos={tot['lidos']} novos={tot['novos']} "
                           f"atualizados={tot['atualizados']} ignorados={tot['ignorados']} silver={sres} gold={gres}")
            ex.finalizado_em = datetime.now(UTC)
            parcial = getattr(leitor, "ultima_parcial", None)
            if parcial:  # teto de lotes da SIL: amostra de 1 lote com mais dados na origem (não é a base inteira)
                alerta = parcial.get("alerta")
                ex.mensagem = (f"parcial: {parcial['lotes_lidos']} lote(s) lido(s), há mais na origem. "
                               + (f"ALERTA {alerta['motivo']}: {alerta['ccms_distintos']} CCM distinto(s) em "
                                  f"{alerta['itens']} itens. " if alerta else "") + ex.mensagem)
                db.add(Log(execucao_id=ex.id, nivel="WARN", mensagem=f"Amostra parcial de {conector}: "
                           f"{parcial['lotes_lidos']} lote(s) lido(s), há mais (teto SIL_MAX_LOTES_POR_EXECUCAO)"))
            db.commit()  # bronze + silver + gold + controle de uma vez
            if parcial:
                from app.core.audit import audita
                audita(db, usuario, "amostra_conector_parcial", f"execucao:{ex.id}", {"conector": conector, **parcial})
        except Exception as exc:  # noqa: BLE001
            db.rollback()  # nada parcial na bronze/silver
            ex = db.get(Execucao, ex.id)
            ex.status = "falha"
            ex.registra_erro(exc)
            ex.mensagem = f"Falha na amostra de {conector}: {ex.erro_publico()['motivo']}"
            ex.finalizado_em = datetime.now(UTC)
            db.add(Log(execucao_id=ex.id, nivel="ERROR", mensagem=ex.mensagem))
            db.commit()
        return ex
    finally:
        if propria:
            _solta_trava(trava, sistema)


def _amostra_iss(db: Session, usuario: str, cfg: ConfigSqlServer, fallback: bool, origem_banco: str, leitor,
                 ex: Execucao | None = None):
    """ISS: mesma rota/lock/auditoria; leitura e classificação do Munícipe (#6), também transacional."""
    from app.services.carga_municipe import executar_carga_municipe
    ex = executar_carga_municipe(db, usuario, connector=leitor or SqlServerConnector(cfg, nome="iss"), bronze=True,
                                 ex=ex)
    db.add(Log(execucao_id=ex.id, nivel="WARN" if fallback else "INFO",
               mensagem=f"Banco de origem do conector iss: {cfg.database or '-'} via {origem_banco}"))
    if ex.status == "falha" and ex.erro_publico():
        ex.mensagem = f"Falha na amostra de iss: {ex.erro_publico()['motivo']}"
    db.commit()
    return ex


def _erro_limpo(exc: Exception) -> str:
    """Só o tipo do erro: a mensagem do driver pode trazer host ou usuário."""
    if isinstance(exc, TimeoutError) or "timeout" in type(exc).__name__.lower():
        return "origem não respondeu (timeout)"
    return f"erro na origem ({type(exc).__name__})"
