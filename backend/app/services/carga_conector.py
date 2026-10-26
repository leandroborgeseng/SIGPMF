"""Carga real por conector (PB12/PB13): amostra via política de tabelas, transacional e auditada.

- Banco de origem por conector: SQLSERVER_DATABASE_<CONECTOR>; sem ele, SQLSERVER_DATABASE (fallback registrado
  no controle). Quando o catálogo do #7 (controle.bases_catalogo) entrar, o nome do banco vem de lá.
- Lê TODAS as tabelas antes de gravar: falha na origem (timeout, erro) não deixa carga parcial na bronze.
- Bronze + silver + gold numa única transação; erro -> ROLLBACK e controle registra status, erro e fim.
- Uma amostra por conector por vez: 409 se já houver uma em andamento (advisory lock + status).
- Nenhum host, usuário ou senha em mensagem, log ou resposta.
"""
import zlib
from datetime import UTC, datetime

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.bronze.ingestao import ingere
from app.connectors.sqlserver.connector import ConfigSqlServer, SqlServerConnector
from app.connectors.sqlserver.politicas import politica
from app.core.config import get_settings
from app.gold import visao
from app.ingestion.mapeamentos import MAPEAMENTOS, NOMES_SISTEMAS
from app.models.controle import BaseCatalogo, Execucao, Log, Sistema, TabelaCatalogo
from app.silver import processamento

# id do conector na API -> sistema da política / mapeamento e banco de origem esperado (PB12)
CONECTORES = {"saude": ("SAUDE", "Saude"), "cemiterio": ("CEMITERIO", "Cemiterio"), "iss": ("ISS", "ISS")}
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


def executar_amostra(db: Session, usuario: str, conector: str, leitor=None) -> Execucao:
    """`leitor`: objeto com ler_amostra(tabela, politica=...) (testes injetam origem sintética)."""
    if conector not in CONECTORES:
        raise ConectorDesconhecido(conector)
    sistema, _banco_esperado = CONECTORES[conector]
    tabelas = tabelas_do_conector(sistema)
    if not tabelas:
        raise SemMapeamento(f"Conector {conector} ainda sem mapeamento para a silver nesta versão.")
    pol = politica(sistema)
    lock = zlib.crc32(f"amostra:{sistema}".encode())
    # conexão dedicada: o lock de sessão não pode depender da conexão que a Session troca a cada commit
    trava = db.get_bind().connect()
    if not trava.execute(text("select pg_try_advisory_lock(:k)"), {"k": lock}).scalar():
        trava.close()
        raise Conflito(f"Já existe uma amostra de {conector} em andamento.")
    try:
        if db.scalar(select(Execucao.id).where(Execucao.tipo.in_(TIPOS_AMOSTRA), Execucao.sistema == sistema,
                                               Execucao.status == "em_andamento")):
            raise Conflito(f"Já existe uma amostra de {conector} em andamento.")
        cfg, fallback = config_do_conector(conector, db)
        especifico = getattr(get_settings(), f"sqlserver_database_{conector}", None)
        origem_banco = ("SQLSERVER_DATABASE (fallback)" if fallback else f"SQLSERVER_DATABASE_{conector.upper()}"
                        if especifico else "catálogo (controle.bases_catalogo)")
        if sistema == "ISS":
            return _amostra_iss(db, usuario, cfg, fallback, origem_banco, leitor)
        ex = Execucao(tipo=TIPO_AMOSTRA, sistema=sistema, status="em_andamento", usuario=usuario)
        db.add(ex)
        db.commit()
        db.add(Log(execucao_id=ex.id, nivel="WARN" if fallback else "INFO",
                   mensagem=f"Banco de origem do conector {conector}: {cfg.database or '-'} via {origem_banco}"))
        db.commit()
        leitor = leitor or SqlServerConnector(cfg, nome=conector)
        tot = {"lidos": 0, "novos": 0, "atualizados": 0, "ignorados": 0, "lotes": 0}
        try:
            lidas = {t: leitor.ler_amostra(t, politica=pol) for t in tabelas}  # tudo lido antes de gravar
            _garante_catalogo(db, sistema, tabelas)
            for t, linhas in lidas.items():
                m = MAPEAMENTOS[(sistema, t)]
                c = ingere(db, sistema, t, m["chave"], m["campo_incremental"], linhas, ex.id,
                           get_settings().lote_tamanho, rowversion=m.get("rowversion"), confirmar=False)
                for k in tot:
                    tot[k] += c[k]
            sres = processamento.reconstruir(db)
            gres = visao.reconstruir(db)
            for k, v in tot.items():
                setattr(ex, k, v)
            ex.status = "sucesso" if (tot["novos"] or tot["atualizados"]) else "sem_novidades"
            ex.mensagem = (f"banco={'fallback' if fallback else 'conector'} lidos={tot['lidos']} novos={tot['novos']} "
                           f"atualizados={tot['atualizados']} ignorados={tot['ignorados']} silver={sres} gold={gres}")
            ex.finalizado_em = datetime.now(UTC)
            db.commit()  # bronze + silver + gold + controle de uma vez
        except Exception as exc:  # noqa: BLE001
            db.rollback()  # nada parcial na bronze/silver
            ex = db.get(Execucao, ex.id)
            ex.status = "falha"
            ex.mensagem = f"Falha na amostra de {conector}: {_erro_limpo(exc)}"
            ex.finalizado_em = datetime.now(UTC)
            db.add(Log(execucao_id=ex.id, nivel="ERROR", mensagem=ex.mensagem))
            db.commit()
        return ex
    finally:
        trava.execute(text("select pg_advisory_unlock(:k)"), {"k": lock})
        trava.commit()
        trava.close()


def _amostra_iss(db: Session, usuario: str, cfg: ConfigSqlServer, fallback: bool, origem_banco: str, leitor):
    """ISS: mesma rota/lock/auditoria; leitura e classificação do Munícipe (#6), também transacional."""
    from app.services.carga_municipe import executar_carga_municipe
    ex = executar_carga_municipe(db, usuario, connector=leitor or SqlServerConnector(cfg, nome="iss"))
    db.add(Log(execucao_id=ex.id, nivel="WARN" if fallback else "INFO",
               mensagem=f"Banco de origem do conector iss: {cfg.database or '-'} via {origem_banco}"))
    if ex.status == "falha" and ex.mensagem and "Falha:" in ex.mensagem:
        ex.mensagem = f"Falha na amostra de iss: erro na origem ({ex.mensagem.split('Falha: ')[-1]})"
    db.commit()
    return ex


def _erro_limpo(exc: Exception) -> str:
    """Só o tipo do erro: a mensagem do driver pode trazer host ou usuário."""
    if isinstance(exc, TimeoutError) or "timeout" in type(exc).__name__.lower():
        return "origem não respondeu (timeout)"
    return f"erro na origem ({type(exc).__name__})"
