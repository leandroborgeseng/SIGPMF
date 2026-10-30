import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.schemas import (
    AgendaIn,
    CargaIn,
    ConfirmaObitoIn,
    ExecutarBaseIn,
    InterromperIn,
    LoginIn,
    RevelarIn,
    SistemaIn,
    TabelaIn,
    UsuarioIn,
)
from app.connectors.sqlserver.connector import ConfigSqlServer, SqlServerConnector
from app.connectors.sqlserver.politicas import TabelaRecusada, politica
from app.core.audit import audita
from app.core.config import VERSION, get_settings
from app.core.db import get_db
from app.core.security import ADMIN, CARGA, confere_senha, cria_token, hash_senha, usuario_atual
from app.explorador.servico import QUAR_OCULTA
from app.gold import visao
from app.gold.visao import CAMPOS_DATA
from app.identity.cpf import mascara, normaliza
from app.identity.documento import mascara as mascara_documento
from app.models.camadas import (
    Cidadao,
    CidadaoSistemas,
    CorrelacaoPendente,
    DadoGold,
    Endereco,
    Identificador,
    Inconsistencia,
    Municipe,
    MunicipePendencia,
    RegistroBronze,
    Vinculo,
)
from app.models.controle import Auditoria, BaseCatalogo, ConfirmacaoObito, Execucao, Log, Sistema, TabelaCatalogo, Usuario
from app.services import acesso_secretaria as acs
from app.services import agenda as ag
from app.services import carga_completa as ccomp
from app.services import carga_conector as cc
from app.services.indicadores import TIPOS_AMOSTRA, bloqueio, indicadores, totais, ultima_execucao
from app.services.pipeline import executar_demo
from app.silver import processamento
from app.util.datas import normaliza_data_iso

publico = APIRouter()
api = APIRouter(dependencies=[Depends(usuario_atual)])


def _ip(req: Request) -> str | None:
    return req.client.host if req.client else None


def _dominios_cid(db: Session, cid: str) -> list[str]:
    """Domínios do cidadão no ouro (para achar a secretaria DONA). Cidadão inexistente = [] (403 antes do 404)."""
    c = db.get(CidadaoSistemas, cid)
    return list(c.dominios or []) if c else []


dom = acs.dom


_AREA_DOMINIO = {"impostos": "tributario"}  # área da linha do tempo -> domínio do ouro


def _menor_cid(db: Session, cid: str) -> bool:
    """R4: o cidadão é menor (ou sem nascimento: trata como menor). Só lê quando a regra está ligada."""
    if not acs.rbac_ativo():
        return False
    c = db.get(Cidadao, cid)
    return _menor(c.data_nascimento if c else None)


_log_health = logging.getLogger(__name__)


@publico.get("/health")
def health(db: Session = Depends(get_db)):
    try:
        db.execute(text("select 1"))
    except Exception:  # noqa: BLE001
        return JSONResponse(status_code=503, content={"status": "erro", "db": "down", "versao": VERSION, "migracao": None})
    return {"status": "ok", "db": "ok", "versao": VERSION, "migracao": _migracao(db)}


def _migracao(db: Session) -> str | None:
    """version_num do alembic (só o id da revisão, sem dado sensível). Falha -> None; nunca derruba o health."""
    try:
        with db.begin_nested():
            v = db.execute(text("select version_num from controle.alembic_version limit 1")).scalar()
        return str(v) if v is not None else None
    except Exception as exc:  # noqa: BLE001 - só a classe no log, nada na resposta
        _log_health.warning("health: migracao ilegivel (%s)", type(exc).__name__)
        return None


@publico.post("/auth/login")
def login(dados: LoginIn, req: Request, db: Session = Depends(get_db)):
    u = db.scalar(select(Usuario).where(Usuario.email == dados.login, Usuario.ativo.is_(True)))
    if u is None or not confere_senha(dados.senha, u.senha_hash):
        audita(db, dados.login[:200], "login_falha", "auth", ip=_ip(req))
        raise HTTPException(401, "Usuário ou senha inválidos")
    audita(db, u.email, "login", "auth", ip=_ip(req))
    return {"access_token": cria_token(u), "token_type": "bearer", "perfil": u.perfil, "nome": u.nome}


@api.get("/auth/me")
def me(u: Usuario = Depends(usuario_atual)):
    return {"email": u.email, "nome": u.nome, "perfil": u.perfil}


# ---------- Dashboard ----------
@api.get("/dashboard")
def dashboard(db: Session = Depends(get_db)):
    def n(m):
        return db.scalar(select(func.count()).select_from(m)) or 0
    ult = db.scalar(select(Execucao).order_by(Execucao.id.desc()).limit(1))
    return {
        "sistemas": n(Sistema), "tabelas": n(TabelaCatalogo),
        # #35: linha SEI reclassificada para restrito não aparece nem na contagem (mesmo filtro dos indicadores)
        "bronze": {"registros": db.scalar(select(func.count()).select_from(RegistroBronze)
                                          .where(RegistroBronze.quarentena.is_distinct_from(QUAR_OCULTA))) or 0},
        "silver": {"cidadaos": n(Cidadao), "vinculos": n(Vinculo), "pendentes": n(CorrelacaoPendente),
                   "inconsistencias": n(Inconsistencia)},
        "gold": {"cidadaos": n(CidadaoSistemas), "dados": n(DadoGold)},
        "ultima_execucao": _exec(ult) if ult else None,
    }


def _exec(e: Execucao) -> dict:
    return {"id": e.id, "tipo": e.tipo, "status": e.status, "usuario": e.usuario, "iniciado_em": e.iniciado_em,
            "finalizado_em": e.finalizado_em, "lidos": e.lidos, "novos": e.novos, "atualizados": e.atualizados,
            "ignorados": e.ignorados, "lotes": e.lotes, "duplicados": e.duplicados, "pendentes": e.pendentes,
            "mensagem": e.mensagem, "erro": e.erro_publico(), "alerta": e.alerta_publico()}


# ---------- Catálogo ----------
def _sis(s: Sistema, tabs: list[TabelaCatalogo]) -> dict:
    return {"id": s.id, "codigo": s.codigo, "nome": s.nome, "descricao": s.descricao, "dominio": s.dominio, "ativo": s.ativo,
            "tabelas": [{"id": t.id, "nome": t.nome, "chave": t.chave, "campo_incremental": t.campo_incremental} for t in tabs]}


@api.get("/catalogo/sistemas")
def lista_sistemas(db: Session = Depends(get_db)):
    tabs = list(db.scalars(select(TabelaCatalogo).order_by(TabelaCatalogo.nome)))
    return [_sis(s, [t for t in tabs if t.sistema_id == s.id]) for s in db.scalars(select(Sistema).order_by(Sistema.codigo))]


@api.post("/catalogo/sistemas", status_code=201)
def cria_sistema(dados: SistemaIn, req: Request, u: Usuario = Depends(ADMIN), db: Session = Depends(get_db)):
    s = Sistema(**dados.model_dump())
    db.add(s)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(422, "Já existe sistema com esse código") from None
    audita(db, u.email, "alterar_catalogo", f"sistema:{s.codigo}", {"operacao": "criar", **dados.model_dump()}, _ip(req))
    return _sis(s, [])


@api.put("/catalogo/sistemas/{sid}")
def altera_sistema(sid: int, dados: SistemaIn, req: Request, u: Usuario = Depends(ADMIN), db: Session = Depends(get_db)):
    s = db.get(Sistema, sid) or _404()
    for k, v in dados.model_dump().items():
        setattr(s, k, v)
    audita(db, u.email, "alterar_catalogo", f"sistema:{s.codigo}", {"operacao": "alterar", **dados.model_dump()}, _ip(req))
    return _sis(s, list(db.scalars(select(TabelaCatalogo).where(TabelaCatalogo.sistema_id == sid))))


@api.post("/catalogo/sistemas/{sid}/tabelas", status_code=201)
def cria_tabela(sid: int, dados: TabelaIn, req: Request, u: Usuario = Depends(ADMIN), db: Session = Depends(get_db)):
    s = db.get(Sistema, sid) or _404()
    pol = politica(s.codigo)
    if pol is not None:
        try:
            pol.autoriza(dados.nome)
        except TabelaRecusada as exc:
            audita(db, u.email, "tabela_recusada", f"{s.codigo}.{dados.nome}", {"codigo": exc.codigo, "motivo": str(exc)},
                   _ip(req))
            raise HTTPException(422, {"codigo": exc.codigo, "mensagem": "Tabela recusada pela política de dados."}) from None
    t = TabelaCatalogo(sistema_id=sid, **dados.model_dump())
    db.add(t)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(422, "Tabela já cadastrada neste sistema") from None
    audita(db, u.email, "alterar_catalogo", f"tabela:{s.codigo}.{t.nome}", {"operacao": "criar", **dados.model_dump()}, _ip(req))
    return {"id": t.id, **dados.model_dump()}


def admin_auditado(req: Request, u: Usuario = Depends(usuario_atual), db: Session = Depends(get_db)) -> Usuario:
    """Como ADMIN, mas o 403 também fica auditado (rotas de carga por conector)."""
    if u.perfil != "admin":
        audita(db, u.email, "acesso_negado", req.url.path, {"perfil": u.perfil}, _ip(req))
        raise HTTPException(403, "Perfil sem permissão para esta ação")
    return u


def _404():
    raise HTTPException(404, "Não encontrado")


# ---------- Conectores e execuções ----------
@api.get("/conectores")
def conectores(db: Session = Depends(get_db)):
    """Um conector por sistema do catálogo, com status e última execução."""
    from app.services import contagens_cache
    from app.services.indicadores import contagens_por_sistema
    cfg = ConfigSqlServer.do_ambiente(get_settings())  # só lê variáveis de ambiente; não conecta na origem
    cont, atualizando = contagens_cache.obter(db, "conectores", contagens_por_sistema)
    bases = indicadores(db, cont, frio=cont is None)
    execs = list(db.scalars(select(Execucao).order_by(Execucao.id.desc()).limit(50)))
    lista = []
    for s, tabs in [(x["codigo"], x) for x in lista_sistemas(db)]:
        ult = next((e for e in execs if e.sistema in (s, "DEMO")), None)
        status = "Nunca executado" if ult is None else ("Com erro" if ult.status == "falha" else "Ativo")
        lista.append({**tabs, "status": status, "ultima_execucao": _exec(ult) if ult else None,
                      "credencial": "Configurada via variável de ambiente" if cfg.resumo_publico()["configurado"] else "Não configurada",
                      "amostra_limite": cfg.amostra_limite, **_banco_origem(s, db)})
    return {"conectores": lista, "execucoes": [_exec(e) for e in execs[:20]], "bases": bases,
            "totais": totais(bases), "carga_habilitada": carga_habilitada(),
            "contagens_atualizando": atualizando or cont is None}


def _id_conector(db: Session, conector: str) -> str:
    """Aceita o id do conector (saude/cemiterio/iss) ou o id numérico da base do catálogo (usa o `conjunto`)."""
    if conector.isdigit():
        b = db.get(BaseCatalogo, int(conector))
        return (b.conjunto or "") if b else ""
    return conector


def _banco_origem(codigo: str, db: Session | None = None) -> dict:
    """PB12: banco de origem do conector (nome do banco, nunca host/usuário). Sem #7, vem de SQLSERVER_DATABASE_<X>."""
    cid = next((k for k, (sis, _b) in cc.CONECTORES.items() if sis == codigo), None)
    if cid is None:
        return {}
    if cid in cc.CONECTORES_API:
        return {"conector_id": cid, "banco_origem": "API SIL", "banco_origem_esperado": cc.CONECTORES[cid][1],
                "banco_origem_fallback": False}
    c, fallback = cc.config_do_conector(cid, db)
    return {"conector_id": cid, "banco_origem": c.database, "banco_origem_esperado": cc.CONECTORES[cid][1],
            "banco_origem_fallback": fallback}


@api.post("/conectores/{conector}/testar")
def testar(conector: str, req: Request, u: Usuario = Depends(CARGA), db: Session = Depends(get_db)):
    if db.scalar(select(Sistema).where(Sistema.codigo == conector)) is None and conector != "sqlserver":
        _404()
    r = SqlServerConnector(ConfigSqlServer.do_ambiente(get_settings()), nome=conector).testar_conexao()
    audita(db, u.email, "testar_conector", f"conector:{conector}", {"ok": r["ok"]}, _ip(req))
    return r


AMBIENTES_CARGA = ("dev", "test")  # allowlist explícita: APP_ENV ausente (padrão "prod") ou qualquer outro valor não carregam


def carga_habilitada() -> bool:
    return get_settings().app_env in AMBIENTES_CARGA


@api.post("/conectores/demo/executar")
def executa_demo(req: Request, dados: CargaIn | None = None, u: Usuario = Depends(CARGA), db: Session = Depends(get_db)):
    if not carga_habilitada():  # carga sintética: 404 fora de APP_ENV exatamente dev/test (auditado)
        audita(db, u.email, "carga_demo_negada", "conector:demo", {"ambiente": get_settings().app_env}, _ip(req))
        _404()
    completa = bool(dados and dados.completa)
    if completa and u.perfil != "admin":
        audita(db, u.email, "carga_completa_negada", "conector:demo", ip=_ip(req))
        raise HTTPException(403, "Carga acima do limite de amostra exige perfil admin")
    return _exec(executar_demo(db, u.email, completa=completa, conjunto=dados.conjunto if dados else "base"))


# Rotas fixas da SIL antes de /conectores/{conector}/amostra (a ordem decide o casamento).
@api.post("/conectores/sil/amostra", status_code=202)
def sil_amostra(req: Request, u: Usuario = Depends(admin_auditado), db: Session = Depends(get_db)):
    """SIL9 (TL 27/10): amostra SIL (consultarlote, UM lote) só para admin, auditada, em background (202).
    Auditoria: usuário, horário (criado_em), id da execução e contagens; nunca documentos, URL ou credenciais."""
    from app.connectors.sil.cliente import SilDesabilitado
    from app.services import amostra_sil as sil
    audita(db, u.email, "amostra_sil_solicitada", "conector:sil", ip=_ip(req))
    try:
        ex = sil.iniciar_amostra(db, u.email)
    except SilDesabilitado as exc:
        audita(db, u.email, "amostra_sil_recusada", "conector:sil", {"motivo": "desligado"}, _ip(req))
        raise HTTPException(409, str(exc)) from None
    except sil.Conflito as exc:
        audita(db, u.email, "amostra_sil_conflito", "conector:sil", ip=_ip(req))
        raise HTTPException(409, str(exc)) from None
    audita(db, u.email, "amostra_sil_iniciada", f"execucao:{ex.id}", {"conector": "sil", "lotes": sil.lotes_por_execucao(),
                                                                     "aprovacao": "manual"},
           _ip(req))
    db.expire_all()  # disparo síncrono nos testes: devolve o estado final
    return _exec_sil(db.get(Execucao, ex.id))


def _exec_sil(ex: Execucao) -> dict:
    return {**_exec(ex), "etapa": ex.etapa, "atualizado_em": ex.atualizado_em, "em_andamento": ex.status == "em_andamento"}


@api.get("/conectores/sil/amostra/{eid}")
def sil_amostra_situacao(eid: int, u: Usuario = Depends(admin_auditado), db: Session = Depends(get_db)):
    from app.connectors.sil import carga
    ex = db.get(Execucao, eid)
    if ex is None or ex.tipo != carga.TIPO_EXECUCAO:
        _404()
    return _exec_sil(ex)


@api.post("/conectores/{conector}/amostra", status_code=202)
def amostra_conector(conector: str, req: Request, u: Usuario = Depends(admin_auditado), db: Session = Depends(get_db)):
    """PB13: carga real por conector, só pela política de tabelas, TOP (AMOSTRA_LIMITE). Admin, auditado.
    TL 27/10: responde 202 com o id da execução e roda em background (como a carga completa); a tela acompanha por
    GET /conectores/{conector}/amostra/{id}. 404/409 continuam imediatos (validação e trava #20 antes do 202)."""
    conector = _id_conector(db, conector)
    audita(db, u.email, "amostra_conector_solicitada", f"conector:{conector}", ip=_ip(req))
    try:
        ex = cc.iniciar_amostra(db, u.email, conector)
    except cc.ConectorDesconhecido:
        _404()
    except cc.SemMapeamento as exc:
        audita(db, u.email, "amostra_conector_recusada", f"conector:{conector}", {"motivo": "sem_mapeamento"}, _ip(req))
        raise HTTPException(409, str(exc)) from None
    except cc.Conflito as exc:
        audita(db, u.email, "amostra_conector_conflito", f"conector:{conector}", ip=_ip(req))
        raise HTTPException(409, str(exc)) from None
    audita(db, u.email, "amostra_conector_iniciada", f"execucao:{ex.id}",
           {"conector": conector, "aprovacao": "manual", **_origem_api(conector)},
           _ip(req))
    db.expire_all()  # se o disparo foi síncrono (testes), devolve o estado final
    return _exec_amostra(db.get(Execucao, ex.id))


def _origem_api(conector: str) -> dict:
    """SIL pela rota genérica (id "sil" ou id numérico da base "API SIL"): unificação TL 28/10, o leitor só fala
    com a SIL pelo ClienteSil (allowlist, sandbox primeiro) e lê no máximo SIL_MAX_LOTES_POR_EXECUCAO lotes."""
    if conector not in cc.CONECTORES_API:
        return {}
    from app.connectors.sil.config import ConfigSil
    return {"cliente": "ClienteSil", "allowlist": True,
            "max_lotes_por_execucao": ConfigSil.do_ambiente().max_lotes_por_execucao,
            **ConfigSil.do_ambiente().origem_publica()}


def _exec_amostra(ex: Execucao) -> dict:
    return {**_exec(ex), "etapa": ex.etapa, "processadas_etapa": ex.processadas_etapa, "total_etapa": ex.total_etapa,
            "atualizado_em": ex.atualizado_em, "em_andamento": ex.status == "em_andamento"}


@api.post("/conectores/{conector}/amostra/{eid}/aprovar")
def amostra_aprovar(conector: str, eid: int, req: Request, u: Usuario = Depends(admin_auditado),
                    db: Session = Depends(get_db)):
    """PR-A (CT2/CT4): "Aprovar amostra" libera a carga completa. Só admin, auditado (quem, quando, SHA). Não lê a origem."""
    conector = _id_conector(db, conector)
    if conector not in cc.CONECTORES:
        _404()
    try:
        return ccomp.aprovar_amostra(db, u.email, conector, eid, _ip(req))
    except ccomp.AprovacaoRecusada as exc:
        audita(db, u.email, "amostra_aprovacao_recusada", f"execucao:{eid}", {"conector": conector}, _ip(req))
        raise HTTPException(409, str(exc)) from None


@api.get("/conectores/{conector}/amostra/{eid}")
def amostra_situacao(conector: str, eid: int, u: Usuario = Depends(admin_auditado), db: Session = Depends(get_db)):
    """Status da amostra em background: status, etapa (leitura/prata/ouro/concluida), contador e sinal de vida."""
    conector = _id_conector(db, conector)
    if conector not in cc.CONECTORES:
        _404()
    ex = db.get(Execucao, eid)
    if ex is None or ex.sistema != cc.CONECTORES[conector][0] or ex.tipo not in cc.TIPOS_AMOSTRA:
        _404()
    return _exec_amostra(ex)


@api.get("/conectores/sil/status")
def sil_status(u: Usuario = Depends(admin_auditado)):
    """Conector SIL (somente leitura): se está ligado e quais consultas estão mapeadas. Nunca devolve URL, id ou
    segredo; não chama a SIL."""
    from app.connectors.sil.cliente import GET_PERMITIDOS
    from app.connectors.sil.config import ConfigSil
    from app.connectors.sil.endpoints import ENDPOINTS
    return {**ConfigSil.do_ambiente().resumo_publico(), "rotas_permitidas": sorted(GET_PERMITIDOS),
            "endpoints": {n: {"caminho": e.caminho, "parametro": e.parametro, "paginado": e.paginado}
                          for n, e in ENDPOINTS.items()}}


@api.get("/conectores/{conector}/bloqueio")
def bloqueio_conector(conector: str, u: Usuario = Depends(usuario_atual), db: Session = Depends(get_db)):
    """CC12: execução (amostra ou completa) que segura o conector: tipo, início, última atividade e se é interrompível."""
    from app.services import trava as tv
    conector = _id_conector(db, conector)
    if conector not in cc.CONECTORES:
        _404()
    est = tv.estado(db, cc.CONECTORES[conector][0], tv.NOMES.get(conector, conector))
    if est is not None:
        est["pode_interromper"] = u.perfil == "admin" and est["parada"]
    return {"conector": conector, "bloqueio": est}


@api.post("/conectores/{conector}/execucoes/{eid}/interromper")
def interromper_execucao(conector: str, eid: int, dados: InterromperIn, req: Request,
                         u: Usuario = Depends(admin_auditado), db: Session = Depends(get_db)):
    """CC12: admin interrompe a execução parada (sem processo vivo ou sem atividade além de SIGPMF_CARGA_ALERTA_MIN).
    Motivo obrigatório, auditado. Nada é apagado; a próxima carga começa sem limpar a bronze."""
    from app.services import carga_gestaoescolar as cge
    from app.services import trava as tv
    conector = _id_conector(db, conector)
    if conector in ("educacao", "gestaoescolar"):  # carga completa da educação (fora de cc.CONECTORES)
        sistema, tipos = cge.SISTEMA, (cge.TIPO_COMPLETA,)
    elif conector in cc.CONECTORES:
        sistema, tipos = cc.CONECTORES[conector][0], tv.TIPOS_CARGA
    else:
        _404()
    ex = db.get(Execucao, eid)
    if ex is None or ex.sistema != sistema or ex.tipo not in tipos:
        _404()
    if ex.status != "em_andamento":
        raise HTTPException(409, "Esta execução não está em andamento.")
    viva = not tv.trava_livre(db, sistema)
    alerta = get_settings().carga_alerta_min
    if viva and tv.parada_min(ex) <= alerta:
        audita(db, u.email, "interromper_execucao_recusado", f"execucao:{eid}", {"motivo": "com_atividade"}, _ip(req))
        raise HTTPException(409, f"A {tv.tipo_rotulo(ex)} tem atividade nos últimos {alerta} min; aguarde ou cancele.")
    tv.interromper(db, ex, u.email, dados.motivo, automatico=False, ip=_ip(req))
    db.commit()
    return {"id": ex.id, "status": ex.status, "mensagem": ex.mensagem}


@api.post("/conectores/{base_id:int}/executar")
def executa_base(base_id: int, req: Request, dados: ExecutarBaseIn | None = None, u: Usuario = Depends(usuario_atual),
                 db: Session = Depends(get_db)):
    """Puxar e processar uma base do catálogo (só admin, sempre auditado).
    amostra: limite AMOSTRA_LIMITE; completa: só depois de uma amostra bem-sucedida; reprocessar: refaz prata/ouro
    a partir do bronze, sem ler a origem. Reusa executar_demo (OrigemMock sintética nesta entrega)."""
    if not carga_habilitada():  # carga real em preparação: rota inexistente fora de dev/test
        _404()
    modo = dados.modo if dados else "amostra"
    if u.perfil != "admin":  # 403 de perfil também fica auditado
        audita(db, u.email, "executar_conector_negado", f"base_id:{base_id}", {"modo": modo, "motivo": "perfil",
                                                                              "perfil": u.perfil}, _ip(req))
        raise HTTPException(403, "Perfil sem permissão para esta ação")
    b = db.get(BaseCatalogo, base_id) or _404()
    motivo = bloqueio(b)
    if motivo is not None:
        audita(db, u.email, "executar_conector_negado", f"base:{b.nome}", {"status": b.status, "modo": modo, "motivo": motivo},
               _ip(req))
        raise HTTPException(422 if b.status == "excluido" else 409, f"{b.nome}: {motivo}.")
    execs = list(db.scalars(select(Execucao).where(Execucao.sistema.is_not(None)).order_by(Execucao.id.desc()).limit(500)))
    if modo == "completa":
        a = ultima_execucao(execs, b, TIPOS_AMOSTRA)
        if a is None or a.status not in ("sucesso", "sem_novidades"):
            audita(db, u.email, "executar_conector_negado", f"base:{b.nome}", {"modo": modo, "motivo": "sem amostra"}, _ip(req))
            raise HTTPException(409, "Carga completa libera depois de uma amostra concluída.")
    audita(db, u.email, "executar_conector", f"base:{b.nome}", {"conjunto": b.conjunto, "modo": modo}, _ip(req))
    if modo == "reprocessar":
        return _exec(_reprocessa(db, u.email, b.conjunto.upper()))
    return _exec(executar_demo(db, u.email, completa=modo == "completa", conjunto=b.conjunto))


def _reprocessa(db: Session, usuario: str, sistema: str) -> Execucao:
    ex = Execucao(tipo="reprocessamento", sistema=sistema, status="em_andamento", usuario=usuario)
    db.add(ex)
    db.commit()
    try:
        sres = processamento.reconstruir(db)
        gres = visao.reconstruir(db)
        ex.status, ex.mensagem = "sucesso", f"silver={sres} gold={gres}"
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        ex = db.get(Execucao, ex.id)
        ex.status, ex.mensagem = "falha", f"Falha: {type(exc).__name__}"
    ex.finalizado_em = datetime.now(UTC)
    audita(db, usuario, "reprocessamento_executado", f"execucao:{ex.id}", {"status": ex.status}, commit=False)
    db.commit()
    return ex


@api.post("/conectores/{conector}/diagnostico")
def diagnostico_conector(conector: str, req: Request, u: Usuario = Depends(admin_auditado),
                         db: Session = Depends(get_db)):
    """Testar conexão: SELECT 1 + DB_NAME() pelo guard. Admin, auditado; só o nome do banco volta."""
    conector = _id_conector(db, conector)
    if conector not in cc.CONECTORES:
        _404()
    cfg, fallback = cc.config_do_conector(conector, db)
    r = _diagnostica(conector, cfg, fallback)
    audita(db, u.email, "diagnostico_conector", f"conector:{conector}", {"ok": r["ok"]}, _ip(req))
    return r


def _diagnostica(conector: str, cfg, fallback: bool) -> dict:
    """Conexão (SELECT 1 + DB_NAME()) e estrutura (política x INFORMATION_SCHEMA), tudo pelo guard."""
    from app.connectors.sqlserver.politicas import politica as pol_de
    from app.services.diagnostico import categoria, motivo
    if conector in cc.CONECTORES_API:  # REST: autenticar já é o teste (não há estrutura SQL a comparar)
        r = cc.leitor_de(conector, cfg).diagnostico()
        r.update({"conector": conector, "banco_configurado": "API SIL", "banco_fallback": False, "estrutura": None})
        return r
    con = SqlServerConnector(cfg, nome=conector)
    r = con.diagnostico()
    r.update({"conector": conector, "banco_configurado": cfg.database, "banco_fallback": fallback, "estrutura": None})
    if r["ok"]:
        try:
            r["estrutura"] = con.verificar_estrutura(pol_de(cc.CONECTORES[conector][0]))
            r["ok"] = r["estrutura"]["ok"]
        except Exception as exc:  # noqa: BLE001
            c = categoria(exc)
            r["ok"] = False
            r["estrutura"] = {"ok": False, "erro": {"classe": c["classe"], "numero": c["numero"],
                                                    "motivo": motivo(c["classe"], c["numero"], None, c["objeto"])}}
    return r


@api.post("/conectores/diagnostico")
def diagnostico_todos(req: Request, u: Usuario = Depends(admin_auditado), db: Session = Depends(get_db)):
    """Todos os conectores numa resposta: tabelas e colunas da política que faltam na origem."""
    out = {}
    for cid in cc.CONECTORES:
        cfg, fallback = cc.config_do_conector(cid, db)
        out[cid] = _diagnostica(cid, cfg, fallback)
    audita(db, u.email, "diagnostico_conectores", "conectores", {k: v["ok"] for k, v in out.items()}, _ip(req))
    return {"ok": all(v["ok"] for v in out.values()), "conectores": out}


def _conector_completa(db: Session, conector: str) -> str:
    conector = _id_conector(db, conector)
    if conector not in cc.CONECTORES:
        _404()
    return conector


@api.get("/conectores/{conector}/completa")
def completa_situacao(conector: str, u: Usuario = Depends(admin_auditado), db: Session = Depends(get_db)):
    """Liberação (CC1), progresso da carga em andamento e card final (lidos/bronze/prata/pendências/reconciliação)."""
    return ccomp.situacao(db, _conector_completa(db, conector))


@api.get("/conectores/{conector}/completa/estimativa")
def completa_estimativa(conector: str, req: Request, u: Usuario = Depends(admin_auditado), db: Session = Depends(get_db)):
    """CC2: COUNT(*) da origem por tabela (pelo guard) para a confirmação."""
    conector = _conector_completa(db, conector)
    motivo = ccomp.motivo_bloqueio(db, conector)
    if motivo:
        raise HTTPException(409, motivo)
    try:
        return ccomp.estimativa(db, conector)
    except ccomp.ChaveInvalida as exc:
        audita(db, u.email, "carga_completa_chave_invalida", f"conector:{conector}", {"motivo": str(exc)}, _ip(req))
        raise HTTPException(409, str(exc)) from None
    except Exception as exc:  # noqa: BLE001 - nunca devolve host/usuário; só o motivo seguro
        from app.services.diagnostico import categoria, motivo
        c = categoria(exc)
        audita(db, u.email, "carga_completa_estimativa_falhou", f"conector:{conector}", {"classe": c["classe"]}, _ip(req))
        raise HTTPException(502, "Não foi possível contar as linhas da origem: "
                                 f"{motivo(c['classe'], c['numero'], c['tabela'], c['objeto'])}") from None


@api.post("/conectores/{conector}/completa", status_code=202)
def completa_conector(conector: str, req: Request, u: Usuario = Depends(admin_auditado), db: Session = Depends(get_db)):
    """Carga completa real (CC3): background, lotes com checkpoint, cancelável. Só admin (CC5), auditada (CC4)."""
    conector = _conector_completa(db, conector)
    audita(db, u.email, "carga_completa_solicitada", f"conector:{conector}", ip=_ip(req))
    try:
        ex = ccomp.iniciar(db, u.email, conector)
    except ccomp.Bloqueado as exc:
        audita(db, u.email, "carga_completa_negada", f"conector:{conector}", {"motivo": str(exc)}, _ip(req))
        raise HTTPException(409, str(exc)) from None
    except cc.Conflito as exc:
        audita(db, u.email, "carga_completa_conflito", f"conector:{conector}", ip=_ip(req))
        raise HTTPException(409, str(exc)) from None
    audita(db, u.email, "carga_completa_iniciada", f"execucao:{ex.id}", {"conector": conector, **_origem_api(conector)},
           _ip(req))
    return ccomp.situacao(db, conector)


@api.get("/conectores/{conector}/completa/quarentena")
def completa_quarentena(conector: str, u: Usuario = Depends(admin_auditado), db: Session = Depends(get_db)):
    """CC9.2: linhas em quarentena (chave nula), só admin, sem valores da linha."""
    return {"linhas": ccomp.quarentena(db, _conector_completa(db, conector))}


@api.post("/conectores/{conector}/completa/retomar-camadas", status_code=202)
def completa_retomar_camadas(conector: str, req: Request, u: Usuario = Depends(admin_auditado),
                             db: Session = Depends(get_db)):
    """CC7: carga interrompida com a leitura concluída retoma só prata e ouro a partir da bronze (sem reler a origem)."""
    conector = _conector_completa(db, conector)
    audita(db, u.email, "carga_completa_retomar_camadas_solicitada", f"conector:{conector}", ip=_ip(req))
    try:
        ex = ccomp.iniciar(db, u.email, conector, somente_camadas=True)
    except ccomp.Bloqueado as exc:
        audita(db, u.email, "carga_completa_negada", f"conector:{conector}", {"motivo": str(exc)}, _ip(req))
        raise HTTPException(409, str(exc)) from None
    except cc.Conflito as exc:
        audita(db, u.email, "carga_completa_conflito", f"conector:{conector}", ip=_ip(req))
        raise HTTPException(409, str(exc)) from None
    audita(db, u.email, "carga_completa_retomada_camadas", f"execucao:{ex.id}", {"conector": conector}, _ip(req))
    return ccomp.situacao(db, conector)


@api.post("/conectores/{conector}/completa/cancelar")
def completa_cancelar(conector: str, req: Request, u: Usuario = Depends(admin_auditado), db: Session = Depends(get_db)):
    """Para no próximo lote; nada do que já entrou é apagado (o checkpoint permite retomar)."""
    conector = _conector_completa(db, conector)
    ex = ccomp.cancelar(db, conector)
    if ex is None:
        raise HTTPException(409, "Não há carga completa em andamento.")
    audita(db, u.email, "carga_completa_cancelamento_pedido", f"execucao:{ex.id}", ip=_ip(req))
    return ccomp.situacao(db, conector)


@api.get("/sistema/recursos")
def sistema_recursos(u: Usuario = Depends(admin_auditado), db: Session = Depends(get_db)):
    """RS1/RS5: disco (volume srvdisk), memória do container e tamanho do banco por camada. Só admin."""
    from app.core import recursos
    return recursos.publico(db)


@api.post("/conectores/iss/municipes/executar", status_code=202)
def executa_municipe(req: Request, u: Usuario = Depends(admin_auditado), db: Session = Depends(get_db)):
    """Compatibilidade com o #6: a carga real só passa pela amostra por conector (PB13), admin e auditada."""
    return amostra_conector("iss", req, u, db)


@api.get("/execucoes/{eid}/logs")
def logs(eid: int, db: Session = Depends(get_db)):
    return [{"nivel": x.nivel, "mensagem": x.mensagem, "criado_em": x.criado_em}
            for x in db.scalars(select(Log).where(Log.execucao_id == eid).order_by(Log.id))]


# ---------- Silver / Gold / Simulador ----------
def _cpfs(db: Session, cids: list[str]) -> dict[str, str]:
    return {i.cidadao_id: i.valor for i in db.scalars(select(Identificador).where(
        Identificador.cidadao_id.in_(cids), Identificador.tipo == "cpf"))}


def _filtra_acesso(q, db: Session, u: Usuario, a: "acs.Acesso"):
    """PR-06: lista só cidadãos com domínio cuja secretaria dona é do usuário; menores só para Educação/prefeito."""
    if not a.ativo or a.dominios is None:
        return q
    from datetime import date as _date

    from sqlalchemy import String, cast, literal
    from sqlalchemy.dialects.postgresql import ARRAY, JSONB
    q = q.where(cast(CidadaoSistemas.dominios, JSONB).op("?|")(cast(literal(sorted(a.dominios)), ARRAY(String))))
    if not acs.ve_menores(db, u):
        h = _date.today()
        try:
            corte = h.replace(year=h.year - 18)
        except ValueError:  # 29/02
            corte = h.replace(year=h.year - 18, day=28)
        q = q.where(CidadaoSistemas.cidadao_id.in_(select(Cidadao.cidadao_id).where(Cidadao.data_nascimento <= corte)))
    return q


@api.get("/silver/cidadaos")
def silver_cidadaos(req: Request, busca: str | None = None, limite: int = 50, offset: int = 0,
                    u: Usuario = Depends(usuario_atual), db: Session = Depends(get_db)):
    a = acs.exige_lista(db, u, req, "lista_cidadaos")
    return _lista_cidadaos(db, u, a, busca, limite, offset)



def _lista_cidadaos(db: Session, u: Usuario, a, busca: str | None, limite: int, offset: int) -> list[dict]:
    q = _filtra_acesso(select(CidadaoSistemas).order_by(CidadaoSistemas.nome), db, u, a)
    digitos = normaliza(busca)
    if busca and digitos and len(digitos) == 11:  # busca pelo CPF completo (resposta continua mascarada)
        q = q.where(CidadaoSistemas.cidadao_id.in_(select(Identificador.cidadao_id).where(Identificador.valor == digitos)))
    elif busca:
        q = q.where(CidadaoSistemas.nome.ilike(f"%{busca}%"))
    rows = list(db.scalars(q.limit(min(limite, 200)).offset(offset)))
    cpfs = _cpfs(db, [r.cidadao_id for r in rows])
    return [{"cidadao_id": r.cidadao_id, "nome": r.nome, "cpf": mascara(cpfs.get(r.cidadao_id)), "sistemas": r.sistemas,
             "dominios": [d for d in (r.dominios or []) if a.ve(d)], "atualizado_em": r.atualizado_em} for r in rows]


@api.get("/silver/cidadaos.csv", response_class=PlainTextResponse)
def exporta_csv(req: Request, u: Usuario = Depends(usuario_atual), db: Session = Depends(get_db)):
    a = acs.exige_lista(db, u, req, "lista_cidadaos_csv")
    rows = _lista_cidadaos(db, u, a, None, 200, 0)
    linhas = ["cidadao_id;nome;cpf;sistemas"] + [
        f"{r['cidadao_id']};{r['nome']};{r['cpf'] or ''};{','.join(r['sistemas'])}" for r in rows]
    return "\n".join(linhas) + "\n"


@api.get("/silver/pendencias", dependencies=[Depends(acs.dom_ou_lgpd("qualidade_dados"))])
def pendencias(db: Session = Depends(get_db)):
    return [{"id": p.id, "sistema": p.sistema, "tabela": p.tabela, "chave_origem": p.chave_origem, "motivo": p.motivo,
             "nome": p.nome, "candidatos": p.candidatos, "detalhe": p.detalhe, "status": p.status}
            for p in db.scalars(select(CorrelacaoPendente).order_by(CorrelacaoPendente.id))]


@api.get("/silver/municipes", dependencies=[Depends(dom("tributario"))])
def silver_municipes(tipo: str | None = None, limite: int = 50, offset: int = 0, db: Session = Depends(get_db)):
    """M7: documento sempre mascarado; empresas (J) não têm cidadao_id (M3)."""
    q = select(Municipe).order_by(Municipe.nome, Municipe.documento)
    if tipo:
        q = q.where(Municipe.tipo == tipo)
    return [{"documento": mascara_documento(m.documento), "tipo": m.tipo, "tipo_pessoa": m.tipo_pessoa, "nome": m.nome,
             "data_nascimento": m.data_nascimento, "data_alteracao": m.data_alteracao, "cidadao_id": m.cidadao_id,
             "confianca": m.confianca} for m in db.scalars(q.limit(min(limite, 200)).offset(offset))]


from app.identity.nome import PERFIL_REVELA_EMPRESA, mascara_nome, mascara_texto  # noqa: E402  (reexporta mascara_nome)


@api.get("/silver/municipes/pendencias", dependencies=[Depends(dom("tributario"))])
def municipes_pendencias(u: Usuario = Depends(usuario_atual), db: Session = Depends(get_db)):
    """M10: perfil leitura recebe 403; operador/admin veem documento e nome só mascarados (só admin_lgpd vê o nome) (a pendência guarda apenas
    a máscara; a chave é um hash)."""
    if u.perfil == "leitura":
        raise HTTPException(403, "Perfil leitura não acessa pendências de Munícipe")
    revela = u.perfil == "admin_lgpd"  # ressalva 3 do Tester: nome só em claro para admin_lgpd
    return [{"chave": p.chave, "motivo": p.motivo, "tipo_pessoa": p.tipo_pessoa,
             "nome": p.nome if revela else mascara_nome(p.nome), "documento": p.documento_mascarado,
             "status": p.status}
            for p in db.scalars(select(MunicipePendencia).order_by(MunicipePendencia.chave))]


@api.get("/silver/inconsistencias", dependencies=[Depends(acs.dom_ou_lgpd("qualidade_dados"))])
def inconsistencias(db: Session = Depends(get_db)):
    return [{"id": i.id, "cidadao_id": i.cidadao_id, "sistema": i.sistema, "chave_origem": i.chave_origem, "tipo": i.tipo,
             "campo": i.campo, "detalhe": i.detalhe} for i in db.scalars(select(Inconsistencia).order_by(Inconsistencia.id))]


@api.get("/gold/cidadaos/{cid}")
def simulador(cid: str, req: Request, u: Usuario = Depends(usuario_atual), db: Session = Depends(get_db)):
    sec = acs.exige(db, u, req, "ficha", dominios=_dominios_cid(db, cid), alvo=f"cidadao:{cid}", menor=_menor_cid(db, cid),
                    revelou_cpf=acs.rbac_ativo())
    c = db.get(CidadaoSistemas, cid) or _404()
    audita(db, u.email, "consulta_ficha", f"cidadao:{cid}", ip=_ip(req))  # FC4: toda abertura de ficha fica registrada
    dados = [d for d in db.scalars(select(DadoGold).where(DadoGold.cidadao_id == cid).order_by(DadoGold.dominio, DadoGold.campo))
             if sec.ve(d.dominio)]
    ends = list(db.scalars(select(Endereco).where(Endereco.cidadao_id == cid).order_by(Endereco.fonte_atualizado_em.desc())))
    return {"cidadao_id": cid, "nome": c.nome or "", "cpf": (_cpfs(db, [cid]).get(cid) if sec.sem_mascara else mascara(_cpfs(db, [cid]).get(cid))),
            "cpf_mascarado": not sec.sem_mascara, "sistemas": (c.sistemas or []) if sec.dominios is None else [],
            "dominios": [d for d in (c.dominios or []) if sec.ve(d)],
            "dados": [{"dominio": d.dominio, "campo": d.campo,
                       "valor": (normaliza_data_iso(d.valor) or d.valor) if d.campo in CAMPOS_DATA else d.valor, "fonte": d.sistema,
                       "atualizado_em": d.fonte_atualizado_em} for d in dados],
            "enderecos": [{"valor": e.logradouro, "fonte": e.sistema, "atualizado_em": e.fonte_atualizado_em,
                           "mais_recente": e.mais_recente} for e in ends]}


def _empresa_sil(e, *, revela: bool) -> dict:
    """SIL16: empresa da SIL para a ficha. Documento sempre mascarado; razão social, endereço e bairro só com revela."""
    from app.services.vinculos import mascara_doc
    return {"ccm": e.ccm, "nome": e.nome if revela else mascara_nome(e.nome), "documento": mascara_doc(e.documento),
            "possui_debito": e.possui_debito, "endereco": mascara_texto(e.endereco, revela),
            "bairro": mascara_texto(e.bairro, revela), "mascarado": not revela}


@api.post("/gold/cidadaos/{cid}/tributario/revelar-empresas")
def tributario_revelar_empresas(cid: str, req: Request, dados: RevelarIn | None = None,
                                u: Usuario = Depends(usuario_atual), db: Session = Depends(get_db)):
    """MÉDIO-1 (decisão do TL): só admin_lgpd revela razão social, endereço e bairro das empresas SIL do cidadão,
    com motivo no corpo (nunca na URL) e auditoria `revelar_empresa_sil`. Espelha o revelar-cpf."""
    from app.models.camadas import EmpresaSil
    if u.perfil != PERFIL_REVELA_EMPRESA:
        audita(db, u.email, "revelar_empresa_sil_negado", f"cidadao:{cid}", {"perfil": u.perfil}, _ip(req))
        raise HTTPException(403, "Só o perfil admin_lgpd revela dados de empresa")
    motivo = (dados.motivo or "").strip() if dados else ""
    if len(motivo) < get_settings().revelar_motivo_min:
        raise HTTPException(400, "Informe o motivo para revelar os dados da empresa.")
    db.get(CidadaoSistemas, cid) or db.get(Cidadao, cid) or _404()
    empresas = list(db.scalars(select(EmpresaSil).where(EmpresaSil.cidadao_id == cid).order_by(EmpresaSil.ccm)))
    audita(db, u.email, "revelar_empresa_sil", f"cidadao:{cid}",
           {"motivo": motivo[:300], "ccms": [e.ccm for e in empresas]}, _ip(req))
    return {"cidadao_id": cid, "empresas": [_empresa_sil(e, revela=True) for e in empresas],
            "remascarar_em_segundos": get_settings().revelar_cpf_segundos}


@api.get("/gold/cidadaos/{cid}/tributario")
def tributario_do_cidadao(cid: str, req: Request, u: Usuario = Depends(usuario_atual), db: Session = Depends(get_db)):
    """TR7: imóveis (posse ativa e histórica), lançamentos e dívida ativa ligados ao cidadão (prata tributária).
    Documento de outros possuidores sai mascarado; a consulta é auditada (dado pessoal + fiscal)."""
    from datetime import date as _date

    from app.models.camadas import DividaAtiva, EmpresaSil, Imovel, ImovelPossuidor, Lancamento
    acs.exige(db, u, req, "tributario", dominios=["tributario"], alvo=f"cidadao:{cid}", menor=_menor_cid(db, cid))
    db.get(CidadaoSistemas, cid) or db.get(Cidadao, cid) or _404()
    empresas = list(db.scalars(select(EmpresaSil).where(EmpresaSil.cidadao_id == cid).order_by(EmpresaSil.ccm)))
    posses = list(db.scalars(select(ImovelPossuidor).where(ImovelPossuidor.cidadao_id == cid)
                             .order_by(ImovelPossuidor.ativo.desc(), ImovelPossuidor.desde.desc())))
    imoveis = {i.codi: i for i in db.scalars(select(Imovel).where(Imovel.codi.in_([p.codi for p in posses])))} if posses else {}
    lancs = list(db.scalars(select(Lancamento).where(Lancamento.cidadao_id == cid)
                            .order_by(Lancamento.vencimento.desc().nulls_last()).limit(200)))
    divs = list(db.scalars(select(DividaAtiva).where(DividaAtiva.cidadao_id == cid).order_by(DividaAtiva.ano.desc())))
    hoje = _date.today()
    f = lambda v: None if v is None else float(v)  # noqa: E731
    em_aberto = [x for x in lancs if not x.pago]
    vencido = [x for x in em_aberto if x.vencimento and x.vencimento < hoje]
    ativa = [d for d in divs if not d.extinta]
    audita(db, u.email, "consulta_tributario", f"cidadao:{cid}", {"imoveis": len(imoveis), "lancamentos": len(lancs),
                                                                  "dividas": len(divs), "empresas": len(empresas)}, _ip(req))
    return {
        "cidadao_id": cid,
        "totais": {"imoveis": len({p.codi for p in posses if p.ativo}),
                   "em_aberto": round(sum(f(x.valor) or 0 for x in em_aberto), 2), "parcelas_em_aberto": len(em_aberto),
                   "vencido": round(sum(f(x.valor) or 0 for x in vencido), 2), "parcelas_vencidas": len(vencido),
                   "divida_ativa": round(sum(f(d.saldo) or 0 for d in ativa), 2), "inscricoes_divida": len(ativa),
                   "protestada": any((d.protestos or 0) > 0 for d in ativa), "empresas": len(empresas)},
        "imoveis": [{"codi": p.codi, "inscricao": getattr(imoveis.get(p.codi), "inscricao", None),
                     "numero": getattr(imoveis.get(p.codi), "numero", None),
                     "complemento": getattr(imoveis.get(p.codi), "complemento", None),
                     "cd_bairro": getattr(imoveis.get(p.codi), "cd_bairro", None),
                     "iptu_anual": f(getattr(imoveis.get(p.codi), "iptu_anual", None)),
                     "tipo_posse": p.tipo_posse, "desde": p.desde, "ate": p.ate, "ativo": p.ativo} for p in posses],
        "lancamentos": [{"exercicio": x.exercicio, "tributo": x.tributo, "parcela": x.parcela, "codi": x.codi,
                         "vencimento": x.vencimento, "valor": f(x.valor), "valor_pago": f(x.valor_pago),
                         "pagamento": x.pagamento, "pago": x.pago,
                         "situacao": "pago" if x.pago else ("vencido" if x.vencimento and x.vencimento < hoje else "a_vencer")}
                        for x in lancs],
        "dividas": [{"codi": d.codi, "ano": d.ano, "natureza": d.natureza, "inscricao": d.inscricao, "saldo": f(d.saldo),
                     "valor_origem": f(d.valor_origem), "protestos": d.protestos or 0, "extinta": d.extinta}
                    for d in divs],
        # API SIL: empresas (inscrições municipais) no CPF do cidadão — MEI, autônomo, empresário individual
        # SIL16 + MÉDIO-1: sempre mascarado aqui, para todos os perfis (inclusive admin_lgpd). Em claro só pelo
        # POST .../tributario/revelar-empresas (admin_lgpd, motivo obrigatório, auditoria revelar_empresa_sil).
        "empresas": [_empresa_sil(e, revela=False) for e in empresas],
    }


@api.get("/gold/cidadaos/{cid}/linha-do-tempo")
def linha_do_tempo(cid: str, req: Request, limite: int = 50, antes: str | None = None, u: Usuario = Depends(usuario_atual),
                   db: Session = Depends(get_db)):
    """SA-F3: eventos do cidadão (todas as áreas), do mais novo para o mais antigo, paginados (`antes`=AAAA-MM-DD)."""
    from datetime import date as _date

    from app.models.camadas import Evento
    sec = acs.exige(db, u, req, "linha_do_tempo", dominios=_dominios_cid(db, cid), alvo=f"cidadao:{cid}",
                    menor=_menor_cid(db, cid))
    db.get(CidadaoSistemas, cid) or db.get(Cidadao, cid) or _404()
    q = select(Evento).where(Evento.cidadao_id == cid)
    if antes:
        try:
            q = q.where(Evento.data < _date.fromisoformat(antes))
        except ValueError:
            raise HTTPException(422, "antes deve ser AAAA-MM-DD") from None
    evs = [e for e in db.scalars(q.order_by(Evento.data.desc(), Evento.id.desc()).limit(max(1, min(limite, 200))))
           if sec.ve(_AREA_DOMINIO.get((e.area or "").lower(), (e.area or "").lower()))]
    audita(db, u.email, "consulta_linha_do_tempo", f"cidadao:{cid}", {"eventos": len(evs)}, _ip(req))
    return {"cidadao_id": cid, "eventos": [{"data": e.data, "area": e.area, "tipo": e.tipo, "titulo": e.titulo,
                                            "fonte": e.fonte} for e in evs]}


@api.get("/gold/cidadaos/{cid}/saude")
def saude_do_cidadao(cid: str, req: Request, u: Usuario = Depends(usuario_atual), db: Session = Depends(get_db)):
    """SA-F5: relacionamento com a Saúde (sem conteúdo clínico): próximos agendamentos, vacinas e últimos atendimentos."""
    from datetime import date as _date

    from app.models.camadas import Agendamento, Atendimento, UnidadeSaude, Vacinacao
    from app.silver.fatos import nomes
    acs.exige(db, u, req, "saude", dominios=["saude"], alvo=f"cidadao:{cid}")  # clínico: só a Saúde (inclusive menor)
    db.get(CidadaoSistemas, cid) or db.get(Cidadao, cid) or _404()
    uni = {x.codigo: x.nome for x in db.scalars(select(UnidadeSaude))}
    esp = nomes(db)["especialidades"]
    hoje = _date.today()
    ag = list(db.scalars(select(Agendamento).where(Agendamento.cidadao_id == cid, Agendamento.data_consulta >= hoje)
                         .order_by(Agendamento.data_consulta).limit(10)))
    at = list(db.scalars(select(Atendimento).where(Atendimento.cidadao_id == cid).order_by(Atendimento.data.desc()).limit(10)))
    va = list(db.scalars(select(Vacinacao).where(Vacinacao.cidadao_id == cid).order_by(Vacinacao.data.desc()).limit(30)))
    audita(db, u.email, "consulta_saude", f"cidadao:{cid}", {"agendamentos": len(ag), "atendimentos": len(at)}, _ip(req))
    return {"cidadao_id": cid,
            "proximos": [{"data": a.data_consulta, "unidade": uni.get(a.unidade), "especialidade": esp.get(a.especialidade),
                          "dias_espera": a.dias_espera} for a in ag],
            "atendimentos": [{"data": a.data, "unidade": uni.get(a.unidade), "especialidade": esp.get(a.especialidade)} for a in at],
            "vacinas": [{"data": v.data, "vacina": v.vacina, "dose": v.dose, "unidade": uni.get(v.unidade)} for v in va]}


@api.get("/indicadores")
def indicadores_gestao(series: str | None = None, db: Session = Depends(get_db)):
    """SA-F4: ouro agregado dos painéis (sem pessoa). `series` = lista separada por vírgula (vazio = todas)."""
    from app.models.camadas import Indicador
    q = select(Indicador)
    if series:
        q = q.where(Indicador.serie.in_([x.strip() for x in series.split(",") if x.strip()]))
    out: dict[str, list] = {}
    atualizado = None
    for i in db.scalars(q.order_by(Indicador.serie, Indicador.periodo, Indicador.dim)):
        out.setdefault(i.serie, []).append({"periodo": i.periodo, "dim": i.dim, "valor": i.valor})
        atualizado = max(atualizado, i.atualizado_em) if atualizado else i.atualizado_em
    return {"atualizado_em": atualizado, "series": out}


@api.get("/gold/cidadaos/{cid}/acessos")
def acessos_ao_cidadao(cid: str, req: Request, u: Usuario = Depends(usuario_atual), db: Session = Depends(get_db)):
    """FC4: quem consultou a ficha deste cidadão (auditoria), mais recente primeiro. Só admin e admin_lgpd; a própria
    consulta também fica registrada."""
    if u.perfil not in ("admin", "admin_lgpd"):
        audita(db, u.email, "acesso_negado", req.url.path, {"perfil": u.perfil}, _ip(req))
        raise HTTPException(403, "Perfil sem permissão para esta ação")
    acs.exige(db, u, req, "acessos", dominios=_dominios_cid(db, cid), alvo=f"cidadao:{cid}", menor=_menor_cid(db, cid))
    db.get(CidadaoSistemas, cid) or db.get(Cidadao, cid) or _404()
    ROTULO = {"consulta_ficha": "Abriu a ficha", "consulta_tributario": "Impostos e imóveis", "consulta_saude": "Saúde", "consulta_linha_do_tempo": "Linha do tempo",
              "revelar_cpf": "Revelou o CPF", "revelar_empresa_sil": "Revelou dados de empresa (SIL)", "consulta_acessos": "Lista de acessos", "consulta_vinculos": "Vínculos"}
    regs = list(db.scalars(select(Auditoria).where(Auditoria.alvo == f"cidadao:{cid}")
                           .order_by(Auditoria.id.desc()).limit(50)))
    audita(db, u.email, "consulta_acessos", f"cidadao:{cid}", {"listados": len(regs)}, _ip(req))
    return {"cidadao_id": cid, "acessos": [{"usuario": a.usuario, "acao": a.acao, "o_que": ROTULO.get(a.acao, a.acao),
                                            "quando": a.criado_em} for a in regs]}


@api.get("/vinculos/{no_id}", dependencies=[Depends(dom("tributario"))])
def explorar_vinculos(no_id: str, req: Request, motivo: str = "", processo: str = "", nivel: int = 0,
                      u: Usuario = Depends(usuario_atual), db: Session = Depends(get_db)):
    """VN1–VN4: vizinhos diretos de um nó (pessoa, imóvel ou possuidor). Só admin e admin_lgpd; motivo obrigatório;
    até NIVEL_MAXIMO níveis a partir do ponto de partida; cada abertura auditada (alvo = cidadão quando é pessoa)."""
    from app.services import vinculos as vn
    if u.perfil not in ("admin", "admin_lgpd"):
        audita(db, u.email, "acesso_negado", req.url.path, {"perfil": u.perfil}, _ip(req))
        raise HTTPException(403, "Perfil sem permissão para esta ação")
    if len(motivo.strip()) < get_settings().revelar_motivo_min:
        raise HTTPException(422, f"Informe o motivo da consulta (mínimo de {get_settings().revelar_motivo_min} caracteres).")
    if nivel > vn.NIVEL_MAXIMO:
        raise HTTPException(422, f"Limite de {vn.NIVEL_MAXIMO} níveis a partir do ponto de partida.")
    try:
        r = vn.explorar(db, no_id, revela_empresa=u.perfil == PERFIL_REVELA_EMPRESA)
    except vn.NoInvalido:
        _404()
    alvo = f"cidadao:{r['no']['cidadao_id']}" if r["no"]["cidadao_id"] else f"vinculo:{r['no']['id']}"
    audita(db, u.email, "consulta_vinculos", alvo, {"no": r["no"]["id"], "nivel": nivel, "motivo": motivo.strip()[:300],
                                                    "processo": processo.strip()[:60], "vizinhos": len(r["vizinhos"])}, _ip(req))
    return {**r, "nivel": nivel, "nivel_maximo": vn.NIVEL_MAXIMO}


@api.get("/politicas")
def politicas_dados(db: Session = Depends(get_db)):
    """PL1: políticas de dados (configuração versionada no código) e a última execução da retenção."""
    from app.catalogo import politicas_dados as pd
    from app.services import retencao
    s = get_settings()
    ult = db.scalar(select(Execucao).where(Execucao.tipo == retencao.TIPO).order_by(Execucao.id.desc()).limit(1))
    regras = [{**r, "texto": r["texto"].format(bronze_dias=s.retencao_bronze_versoes_dias)} for r in pd.REGRAS]
    return {"regras": regras, "conjuntos": pd.CONJUNTOS, "areas": pd.AREAS, "perfis": pd.PERFIS,
            "retencao": {"bronze_versoes_dias": s.retencao_bronze_versoes_dias, "logs_dias": s.retencao_logs_dias,
                         "ultima": None if ult is None else {"status": ult.status, "iniciado_em": ult.iniciado_em,
                                                             "mensagem": ult.mensagem, "apagados": ult.lidos}}}


@api.post("/politicas/retencao/executar")
def politicas_retencao_executar(req: Request, u: Usuario = Depends(admin_auditado), db: Session = Depends(get_db)):
    """PL2: executa a retenção agora (admin, auditado). Só apaga versões antigas da bronze e logs técnicos."""
    from app.services import retencao
    ex = retencao.executar(db, u.email)
    return {"status": ex.status, "mensagem": ex.mensagem, "apagados": ex.lidos}


@api.get("/gold/cidadaos/{cid}/sistemas")
def sistemas_do_cidadao(cid: str, req: Request, u: Usuario = Depends(usuario_atual), db: Session = Depends(get_db)):
    a = acs.exige(db, u, req, "sistemas", dominios=_dominios_cid(db, cid), alvo=f"cidadao:{cid}", menor=_menor_cid(db, cid))
    c = db.get(CidadaoSistemas, cid) or _404()
    # Com a regra ligada, a lista de sistemas (que revela presença em outras áreas) só para o prefeito.
    return {"cidadao_id": cid, "sistemas": c.sistemas if a.dominios is None else [],
            "dominios": [d for d in (c.dominios or []) if a.ve(d)]}


@api.post("/cidadaos/{cid}/revelar-cpf")
def revelar(cid: str, req: Request, dados: RevelarIn | None = None, u: Usuario = Depends(usuario_atual),
            db: Session = Depends(get_db)):
    if u.perfil not in get_settings().perfis_reveal:
        audita(db, u.email, "revelar_cpf_negado", f"cidadao:{cid}", ip=_ip(req))
        raise HTTPException(403, "Perfil sem permissão para revelar CPF")
    motivo = (dados.motivo or "").strip() if dados else ""
    if len(motivo) < get_settings().revelar_motivo_min:
        # PR-06 (TL 29/10): admin_lgpd revela por função, sem vínculo; tentativa sem motivo também é auditada (sem PII)
        audita(db, u.email, "revelar_cpf_negado", f"cidadao:{cid}", {"razao": "motivo_invalido"}, _ip(req))
        raise HTTPException(400, "Informe o motivo para revelar o CPF.")
    cpf = _cpfs(db, [cid]).get(cid)
    if cpf is None:
        _404()
    audita(db, u.email, "revelar_cpf", f"cidadao:{cid}", {"motivo": motivo}, _ip(req))
    return {"cidadao_id": cid, "cpf": cpf, "remascarar_em_segundos": get_settings().revelar_cpf_segundos}


MOTIVOS_OBITO = ("obito_revisao", "obito_ambiguo")
PERFIL_CONFIRMA_OBITO = "admin_lgpd"  # decisão do TL: fixo


@api.post("/silver/pendencias/{pid}/confirmar-obito", dependencies=[Depends(acs.dom_ou_lgpd("qualidade_dados"))])
def confirmar_obito(pid: int, dados: ConfirmaObitoIn, req: Request, u: Usuario = Depends(usuario_atual),
                    db: Session = Depends(get_db)):
    """CM7: só o admin_lgpd confirma; motivo obrigatório; auditado. Nenhum candidato vem pré-selecionado."""
    if u.perfil != PERFIL_CONFIRMA_OBITO:
        audita(db, u.email, "confirmar_obito_negado", f"pendencia:{pid}", ip=_ip(req))
        raise HTTPException(403, "Só o perfil admin_lgpd confirma óbito")
    motivo = (dados.motivo or "").strip()
    if len(motivo) < get_settings().revelar_motivo_min:
        raise HTTPException(400, "Informe o motivo para confirmar o óbito.")
    p = db.get(CorrelacaoPendente, pid) or _404()
    if p.motivo not in MOTIVOS_OBITO:
        raise HTTPException(422, "Pendência não é de óbito com candidato")
    if dados.cidadao_id not in (p.candidatos or []):
        raise HTTPException(422, "Cidadão não está entre os candidatos")
    db.add(ConfirmacaoObito(sistema=p.sistema, tabela=p.tabela, chave_origem=p.chave_origem, cidadao_id=dados.cidadao_id,
                            usuario=u.email, motivo=motivo))
    audita(db, u.email, "confirmar_obito", f"cidadao:{dados.cidadao_id}",
           {"motivo": motivo, "origem": f"{p.sistema}.{p.tabela}:{p.chave_origem}"}, _ip(req), commit=False)
    db.flush()
    processamento.reconstruir(db)
    visao.reconstruir(db)
    db.commit()
    return {"cidadao_id": dados.cidadao_id, "falecido": True}


# ---------- Usuários e auditoria (somente leitura) ----------
@api.post("/usuarios", status_code=201)
def cria_usuario(dados: UsuarioIn, req: Request, u: Usuario = Depends(ADMIN), db: Session = Depends(get_db)):
    novo = Usuario(email=dados.email.lower(), nome=dados.nome, perfil=dados.perfil, senha_hash=hash_senha(dados.senha))
    db.add(novo)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(422, "Usuário já existe") from None
    audita(db, u.email, "criar_usuario", f"usuario:{novo.email}", {"perfil": novo.perfil}, _ip(req))
    return {"email": novo.email, "perfil": novo.perfil}


@api.get("/auditoria")
def auditoria(limite: int = 100, u: Usuario = Depends(ADMIN), db: Session = Depends(get_db)):
    return [{"id": a.id, "usuario": a.usuario, "acao": a.acao, "alvo": a.alvo, "detalhe": a.detalhe, "criado_em": a.criado_em}
            for a in db.scalars(select(Auditoria).order_by(Auditoria.id.desc()).limit(min(limite, 500)))]





# ---------- Painel de progresso por fonte (PP1–PP11) ----------
@api.get("/fontes/progresso")
def fontes_progresso(db: Session = Depends(get_db)):
    """PP1–PP11: status, lote X de ~Y, tabelas, tempo, último sinal e motivo por fonte. Só lê o controle (nunca a
    origem); qualquer perfil que vê a tela de fontes (PP9); só números agregados e reason codes traduzidos."""
    from app.services import progresso
    return progresso.painel(db)


# ---------- Agenda das cargas (AG1–AG6) ----------
@api.get("/agenda")
def agenda_estado(db: Session = Depends(get_db)):
    """Agendamentos, próxima execução, última carga e histórico das últimas 24 h (qualquer perfil autenticado)."""
    try:
        return ag.estado(db)
    except ag.AgendaInvalida as exc:
        raise HTTPException(500, str(exc)) from None


@api.put("/agenda/{conector}")
def agenda_alterar(conector: str, dados: AgendaIn, req: Request, u: Usuario = Depends(admin_auditado),
                   db: Session = Depends(get_db)):
    try:
        ag.atualizar(db, conector, u.email, **dados.model_dump())
    except LookupError:
        _404()
    except ag.AgendaInvalida as exc:
        raise HTTPException(422, str(exc)) from None
    except ag.AgendaTravada:  # B1: motivo e mensagem fixos (nada de str(exc))
        audita(db, u.email, "agenda_alterada_recusada", f"agendamento:{conector}",
               {"motivo": ag.MOTIVO_SIL_TRAVADA, "conector": conector}, _ip(req))
        raise HTTPException(409, ag.MSG_SIL_TRAVADA) from None
    return ag.estado(db)


@api.post("/agenda/{conector}/executar-agora", status_code=202)
def agenda_executar_agora(conector: str, req: Request, u: Usuario = Depends(admin_auditado), db: Session = Depends(get_db)):
    """AG6: o próximo ciclo da agenda (até AGENDA_INTERVALO_S) roda o conector, mesmo fora do horário e da janela."""
    from app.models.controle import Agendamento
    item = db.scalar(select(Agendamento).where(Agendamento.conector == conector))
    if item is None:  # #33 B1: conector inexistente dá 404 antes da agenda; nome da URL não vai para a auditoria
        _404()
    if not get_settings().agenda_habilitada:  # #32: desliga geral também recusa e fica na auditoria
        audita(db, u.email, "agenda_executar_agora_recusada", f"agendamento:{item.conector}",
               {"motivo": "agenda_desligada", "conector": item.conector}, _ip(req))
        raise HTTPException(409, "A agenda está desligada neste ambiente (AGENDA_HABILITADA).")
    if not item.ativo:  # QA main 3094209: agendamento desligado (ex.: sil, 0015) não roda por "Executar agora"
        audita(db, u.email, "agenda_executar_agora_recusada", f"agendamento:{conector}", {"motivo": "desligado", "conector": conector},
               _ip(req))
        raise HTTPException(409, f"O agendamento de {conector} está desligado. Ligue o agendamento antes de "
                                 "executar agora.")
    if conector == ag.EDUCACAO:  # educação: trava de concorrência, amostra e janela (mesma regra da carga manual)
        recusa = ag.educacao_recusa(db)
        if recusa:
            audita(db, u.email, "agenda_executar_agora_recusada", f"agendamento:{conector}",
                   {"motivo": recusa[0], "conector": conector}, _ip(req))
            raise HTTPException(409, recusa[1])
    try:
        ag.forcar(db, conector, u.email)
    except LookupError:
        _404()
    except ag.AgendaDesligada:  # #33 B2: desligado em paralelo entre a checagem acima e a gravação
        audita(db, u.email, "agenda_executar_agora_recusada", f"agendamento:{conector}",
               {"motivo": "desligado", "conector": conector}, _ip(req))
        raise HTTPException(409, f"O agendamento de {conector} está desligado. Ligue o agendamento antes de "
                                 "executar agora.") from None
    return ag.estado(db)


# ---------- Educação (FRA_GestaoEscolar + FRA_CoreSSO): GE4, GE9 e GE11 ----------
PERFIL_REVELA_EDUCACAO = "admin_lgpd"


def _menor(nasc, hoje=None) -> bool:
    from datetime import date as _date
    if nasc is None:
        return True  # sem nascimento: trata como menor (mais restritivo)
    h = hoje or _date.today()
    return (h.year - nasc.year - ((h.month, h.day) < (nasc.month, nasc.day))) < 18


def _educacao(db: Session, cid: str, *, revela: bool) -> list[dict]:
    """GE4: aluno menor sai com nome, CPF, endereço e responsáveis mascarados, salvo `revela` (admin_lgpd + motivo).
    GE9: situação 1 = ativo; situação 3 = exclusão lógica, fora da ficha (aluno e matrícula); qualquer outra
    situação = inativo, visível como histórico e marcado `inativo`. Só a matrícula com situação 1 é a atual."""
    from app.identity.nome import mascara_nome, mascara_texto
    from app.models.camadas import EscolaAluno, EscolaMatricula, EscolaResponsavel
    out = []
    for a in db.scalars(select(EscolaAluno).where(EscolaAluno.cidadao_id == cid, EscolaAluno.status_vinculo == "vinculado",
                                                  EscolaAluno.excluido.is_(False))
                        .order_by(EscolaAluno.alu_id)):
        claro = revela or not _menor(a.data_nascimento)
        mats = list(db.scalars(select(EscolaMatricula).where(EscolaMatricula.alu_id == a.alu_id,
                                                             EscolaMatricula.excluida.is_(False))
                               .order_by(EscolaMatricula.mtu_id.desc())))
        resps = list(db.scalars(select(EscolaResponsavel).where(EscolaResponsavel.alu_id == a.alu_id,
                                                                EscolaResponsavel.excluido.is_(False))
                                .order_by(EscolaResponsavel.alr_id)))
        m = lambda x: {"escola": x.escola, "escola_inep": x.escola_inep, "unidade": x.unidade,  # noqa: E731
                       "unidade_excluida": bool(x.unidade_excluida), "turma": x.turma, "situacao": x.situacao,
                       "inativa": not x.atual, "excluida": x.excluida, "data_alteracao": x.data_alteracao}
        out.append({
            "menor": _menor(a.data_nascimento), "mascarado": not claro,
            "nome": a.nome if claro else mascara_nome(a.nome),
            "cpf": (a.cpf if revela else mascara(a.cpf)),
            "data_nascimento": a.data_nascimento if claro else None,
            "endereco": {"logradouro": mascara_texto(a.logradouro, claro), "numero": mascara_texto(a.numero, claro),
                         "bairro": mascara_texto(a.bairro, claro), "cep": mascara_texto(a.cep, claro)},
            "responsaveis": [{"tipo": r.tipo, "nome": r.nome if claro else mascara_nome(r.nome),
                              "cpf": r.cpf if revela else mascara(r.cpf), "principal": r.principal} for r in resps],
            "excluido": a.excluido, "situacao": a.situacao,
            "inativo": a.situacao != 1,
            "matricula_atual": next((m(x) for x in mats if x.atual), None),
            "historico": [m(x) for x in mats],
        })
    return out


@api.get("/gold/cidadaos/{cid}/educacao")
def educacao_do_cidadao(cid: str, req: Request, u: Usuario = Depends(usuario_atual), db: Session = Depends(get_db)):
    sec = acs.exige(db, u, req, "educacao", dominios=["educacao"], alvo=f"cidadao:{cid}", revelou_cpf=acs.rbac_ativo())
    db.get(CidadaoSistemas, cid) or db.get(Cidadao, cid) or _404()
    # R1 (TL 29/10): com vínculo vigente à Educação, tudo sai sem máscara, inclusive o CPF completo (auditado).
    alunos = _educacao(db, cid, revela=sec.sem_mascara)
    audita(db, u.email, "consulta_educacao", f"cidadao:{cid}", {"alunos": len(alunos)}, _ip(req))
    return {"cidadao_id": cid, "alunos": alunos}


@api.post("/gold/cidadaos/{cid}/educacao/revelar")
def educacao_revelar(cid: str, req: Request, dados: RevelarIn | None = None, u: Usuario = Depends(usuario_atual),
                     db: Session = Depends(get_db)):
    """GE4: só admin_lgpd revela dados de aluno menor, com motivo no corpo (nunca na URL) e auditoria."""
    if u.perfil != PERFIL_REVELA_EDUCACAO:
        audita(db, u.email, "revelar_educacao_negado", f"cidadao:{cid}", {"perfil": u.perfil}, _ip(req))
        raise HTTPException(403, "Só o perfil admin_lgpd revela dados de aluno")
    motivo = (dados.motivo or "").strip() if dados else ""
    if len(motivo) < get_settings().revelar_motivo_min:
        # B2: tentativa sem motivo também fica na auditoria (sem PII: nem o motivo digitado)
        audita(db, u.email, "revelar_educacao_negado", f"cidadao:{cid}",
               {"perfil": u.perfil, "razao": "motivo_invalido"}, _ip(req))
        raise HTTPException(400, "Informe o motivo para revelar os dados do aluno.")
    db.get(CidadaoSistemas, cid) or db.get(Cidadao, cid) or _404()
    alunos = _educacao(db, cid, revela=True)
    audita(db, u.email, "revelar_educacao", f"cidadao:{cid}", {"motivo": motivo[:300], "alunos": len(alunos)}, _ip(req))
    return {"cidadao_id": cid, "alunos": alunos, "remascarar_em_segundos": get_settings().revelar_cpf_segundos}


@api.get("/educacao/releitura-deploy", dependencies=[Depends(ADMIN)])
def releitura_deploy_status(db: Session = Depends(get_db)):
    """Marcadores da releitura de uso único no boot (SIGPMF_RELEITURA_FONTE): fonte, token, status e código fixo."""
    from app.models.controle import AcaoUnica
    from app.services import releitura_deploy as rd
    ms = db.scalars(select(AcaoUnica).where(AcaoUnica.acao == rd.ACAO).order_by(AcaoUnica.id.desc()).limit(50))
    return [{"fonte": m.fonte, "token": m.token, "status": m.status, "codigo": m.codigo, "execucao_id": m.execucao_id,
             "tentativas": m.tentativas, "atualizado_em": m.atualizado_em} for m in ms]


@api.post("/educacao/conector/cancelar", dependencies=[Depends(ADMIN)])
def gestaoescolar_cancelar(req: Request, u: Usuario = Depends(usuario_atual), db: Session = Depends(get_db)):
    """Pede o cancelamento da carga completa em andamento; ela para no fim do lote atual (nada é apagado)."""
    from app.services import carga_gestaoescolar as cge
    try:
        ex = cge.solicita_cancelamento(db)
    except cge.CargaRecusada as exc:
        motivo = cge.mensagem_segura(exc)
        audita(db, u.email, "gestaoescolar_cancelar_negado", "conector:gestaoescolar:completa", {"motivo": motivo}, _ip(req))
        raise HTTPException(409, motivo) from None
    audita(db, u.email, "gestaoescolar_cancelar", f"execucao:{ex.id}",
           {"status": ex.status, "ja_solicitado": ex.ja_solicitado}, _ip(req))
    return {"execucao_id": ex.id, "cancelamento_solicitado": True, "ja_solicitado": ex.ja_solicitado}


@api.post("/educacao/conector/execucoes/{eid:int}/marcar-falha", dependencies=[Depends(ADMIN)])
def gestaoescolar_marcar_falha(eid: int, req: Request, u: Usuario = Depends(usuario_atual), db: Session = Depends(get_db)):
    """Execução órfã: em_andamento e sem batida há mais de SIGPMF_EDUCACAO_ORFA_MIN min vira `falha` (UPDATE atômico)."""
    from app.services import carga_gestaoescolar as cge
    minutos = get_settings().ge_orfa_min
    if not cge.marca_falha_orfa(db, eid):
        ex = db.get(Execucao, eid)
        if ex is None or ex.tipo != cge.TIPO_COMPLETA:
            _404()
        motivo = "nao_em_andamento" if ex.status != "em_andamento" else "batida_recente"
        audita(db, u.email, "gestaoescolar_marcar_falha_negado", f"execucao:{eid}", {"motivo": motivo}, _ip(req))
        raise HTTPException(409, "Esta execução não está em andamento." if motivo == "nao_em_andamento"
                            else cge.BATIDA_RECENTE.format(min=minutos))
    audita(db, u.email, "gestaoescolar_marcar_falha", f"execucao:{eid}", {"orfa_min": minutos}, _ip(req))
    return {"execucao_id": eid, "status": "falha"}


@api.post("/educacao/conector/{modo}", dependencies=[Depends(ADMIN)])
def gestaoescolar_executar(modo: str, req: Request, u: Usuario = Depends(usuario_atual), db: Session = Depends(get_db)):
    """GE3/GE11: amostra (até 50 linhas por tabela), carga completa ou incremental (janela noturna, teto de lotes,
    checkpoint). IN6: a completa só roda por aqui (ADMIN, auditada); a agenda roda a incremental."""
    from app.connectors.gestaoescolar.consultas import ConfigGE, LeitorGE
    from app.services import carga_gestaoescolar as cge
    if modo not in ("amostra", "completa", "incremental"):
        _404()
    cfg = ConfigGE.do_ambiente(get_settings())
    janela_ignorada = fora_da_janela = False
    try:
        cfg.valida()
        if modo in ("completa", "incremental"):
            if not cge.amostra_ok(db):
                raise cge.CargaRecusada(cge.SEM_AMOSTRA, "sem_amostra")
            if cge.completa_em_andamento(db):
                raise cge.CargaRecusada(cge.EM_ANDAMENTO, "em_andamento")
            janela_ignorada = cge.checa_janela(datetime.now(UTC))
            fora_da_janela = not cge.dentro_da_janela(datetime.now(UTC))[0]
    except (PermissionError, cge.CargaRecusada) as exc:
        motivo = cge.mensagem_segura(exc)  # nunca str(exc): pode trazer usuário/senha
        audita(db, u.email, "gestaoescolar_negado", f"conector:gestaoescolar:{modo}", {"motivo": motivo}, _ip(req))
        raise HTTPException(409, motivo) from None
    try:
        with LeitorGE(cfg) as leitor:
            if modo == "amostra":
                ex = cge.amostra(db, leitor, cfg, u.email)
            elif modo == "incremental":
                ex = cge.incremental(db, leitor, cfg, u.email)
            else:
                ex = cge.completa(db, leitor, cfg, u.email)
    except cge.CargaRecusada as exc:  # corrida: trava/índice único (0025) recusou a segunda carga
        motivo = cge.mensagem_segura(exc)  # nunca str(exc): pode trazer usuário/senha
        audita(db, u.email, "gestaoescolar_negado", f"conector:gestaoescolar:{modo}", {"motivo": motivo}, _ip(req))
        raise HTTPException(409, motivo) from None
    detalhe = {"status": ex.status, "lidos": ex.lidos}
    if modo != "amostra":
        detalhe["janela_ignorada"], detalhe["fora_da_janela"] = janela_ignorada, fora_da_janela
    audita(db, u.email, f"gestaoescolar_{modo}", f"execucao:{ex.id}", detalhe, _ip(req))
    return {"execucao_id": ex.id, "status": ex.status, "mensagem": ex.mensagem, "lidos": ex.lidos, "lotes": ex.lotes}
