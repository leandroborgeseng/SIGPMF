from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.schemas import CargaIn, ConfirmaObitoIn, ExecutarBaseIn, LoginIn, RevelarIn, SistemaIn, TabelaIn, UsuarioIn
from app.connectors.sqlserver.connector import ConfigSqlServer, SqlServerConnector
from app.connectors.sqlserver.politicas import TabelaRecusada, politica
from app.core.audit import audita
from app.core.config import VERSION, get_settings
from app.core.db import get_db
from app.core.security import ADMIN, CARGA, confere_senha, cria_token, hash_senha, usuario_atual
from app.gold import visao
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
from app.services.carga_municipe import executar_carga_municipe
from app.services.indicadores import TIPOS_AMOSTRA, bloqueio, indicadores, totais, ultima_execucao
from app.services.pipeline import executar_demo
from app.silver import processamento

publico = APIRouter()
api = APIRouter(dependencies=[Depends(usuario_atual)])


def _ip(req: Request) -> str | None:
    return req.client.host if req.client else None


@publico.get("/health")
def health(db: Session = Depends(get_db)):
    try:
        db.execute(text("select 1"))
        return {"status": "ok", "db": "ok", "versao": VERSION}
    except Exception:  # noqa: BLE001
        return JSONResponse(status_code=503, content={"status": "erro", "db": "down", "versao": VERSION})


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
        "bronze": {"registros": n(RegistroBronze)},
        "silver": {"cidadaos": n(Cidadao), "vinculos": n(Vinculo), "pendentes": n(CorrelacaoPendente),
                   "inconsistencias": n(Inconsistencia)},
        "gold": {"cidadaos": n(CidadaoSistemas), "dados": n(DadoGold)},
        "ultima_execucao": _exec(ult) if ult else None,
    }


def _exec(e: Execucao) -> dict:
    return {"id": e.id, "tipo": e.tipo, "status": e.status, "usuario": e.usuario, "iniciado_em": e.iniciado_em,
            "finalizado_em": e.finalizado_em, "lidos": e.lidos, "novos": e.novos, "atualizados": e.atualizados,
            "ignorados": e.ignorados, "lotes": e.lotes, "duplicados": e.duplicados, "pendentes": e.pendentes,
            "mensagem": e.mensagem}


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
            audita(db, u.email, "tabela_recusada", f"{s.codigo}.{dados.nome}", {"motivo": str(exc)}, _ip(req))
            raise HTTPException(422, str(exc)) from None
    t = TabelaCatalogo(sistema_id=sid, **dados.model_dump())
    db.add(t)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(422, "Tabela já cadastrada neste sistema") from None
    audita(db, u.email, "alterar_catalogo", f"tabela:{s.codigo}.{t.nome}", {"operacao": "criar", **dados.model_dump()}, _ip(req))
    return {"id": t.id, **dados.model_dump()}


def _404():
    raise HTTPException(404, "Não encontrado")


# ---------- Conectores e execuções ----------
@api.get("/conectores")
def conectores(db: Session = Depends(get_db)):
    """Um conector por sistema do catálogo, com status e última execução."""
    cfg = ConfigSqlServer.do_ambiente(get_settings())
    bases = indicadores(db)
    execs = list(db.scalars(select(Execucao).order_by(Execucao.id.desc()).limit(50)))
    lista = []
    for s, tabs in [(x["codigo"], x) for x in lista_sistemas(db)]:
        ult = next((e for e in execs if e.sistema in (s, "DEMO")), None)
        status = "Nunca executado" if ult is None else ("Com erro" if ult.status == "falha" else "Ativo")
        lista.append({**tabs, "status": status, "ultima_execucao": _exec(ult) if ult else None,
                      "credencial": "Configurada via variável de ambiente" if cfg.resumo_publico()["configurado"] else "Não configurada",
                      "amostra_limite": cfg.amostra_limite})
    return {"conectores": lista, "execucoes": [_exec(e) for e in execs[:20]], "bases": bases,
            "totais": totais(bases), "carga_habilitada": carga_habilitada()}


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
    if not carga_habilitada():  # mesma trava da carga por base: 404 fora de dev/test
        _404()
    completa = bool(dados and dados.completa)
    if completa and u.perfil != "admin":
        audita(db, u.email, "carga_completa_negada", "conector:demo", ip=_ip(req))
        raise HTTPException(403, "Carga acima do limite de amostra exige perfil admin")
    return _exec(executar_demo(db, u.email, completa=completa, conjunto=dados.conjunto if dados else "base"))


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


@api.post("/conectores/iss/municipes/executar")
def executa_municipe(req: Request, u: Usuario = Depends(CARGA), db: Session = Depends(get_db)):
    """Amostra controlada (AMOSTRA_LIMITE) de ISS.dbo.Municipes; credencial só pelo ambiente."""
    cfg = ConfigSqlServer.do_ambiente(get_settings())
    if not cfg.resumo_publico()["configurado"]:
        raise HTTPException(400, "Origem ISS não configurada no ambiente.")
    ex = executar_carga_municipe(db, u.email, connector=SqlServerConnector(cfg, nome="ISS"))
    return _exec(ex)


@api.get("/execucoes/{eid}/logs")
def logs(eid: int, db: Session = Depends(get_db)):
    return [{"nivel": x.nivel, "mensagem": x.mensagem, "criado_em": x.criado_em}
            for x in db.scalars(select(Log).where(Log.execucao_id == eid).order_by(Log.id))]


# ---------- Silver / Gold / Simulador ----------
def _cpfs(db: Session, cids: list[str]) -> dict[str, str]:
    return {i.cidadao_id: i.valor for i in db.scalars(select(Identificador).where(
        Identificador.cidadao_id.in_(cids), Identificador.tipo == "cpf"))}


@api.get("/silver/cidadaos")
def silver_cidadaos(busca: str | None = None, limite: int = 50, offset: int = 0, db: Session = Depends(get_db)):
    q = select(CidadaoSistemas).order_by(CidadaoSistemas.nome)
    digitos = normaliza(busca)
    if busca and digitos and len(digitos) == 11:  # busca pelo CPF completo (resposta continua mascarada)
        q = q.where(CidadaoSistemas.cidadao_id.in_(select(Identificador.cidadao_id).where(Identificador.valor == digitos)))
    elif busca:
        q = q.where(CidadaoSistemas.nome.ilike(f"%{busca}%"))
    rows = list(db.scalars(q.limit(min(limite, 200)).offset(offset)))
    cpfs = _cpfs(db, [r.cidadao_id for r in rows])
    return [{"cidadao_id": r.cidadao_id, "nome": r.nome, "cpf": mascara(cpfs.get(r.cidadao_id)), "sistemas": r.sistemas,
             "dominios": r.dominios, "atualizado_em": r.atualizado_em} for r in rows]


@api.get("/silver/cidadaos.csv", response_class=PlainTextResponse)
def exporta_csv(db: Session = Depends(get_db)):
    rows = silver_cidadaos(None, 200, 0, db)
    linhas = ["cidadao_id;nome;cpf;sistemas"] + [
        f"{r['cidadao_id']};{r['nome']};{r['cpf'] or ''};{','.join(r['sistemas'])}" for r in rows]
    return "\n".join(linhas) + "\n"


@api.get("/silver/pendencias")
def pendencias(db: Session = Depends(get_db)):
    return [{"id": p.id, "sistema": p.sistema, "tabela": p.tabela, "chave_origem": p.chave_origem, "motivo": p.motivo,
             "nome": p.nome, "candidatos": p.candidatos, "detalhe": p.detalhe, "status": p.status}
            for p in db.scalars(select(CorrelacaoPendente).order_by(CorrelacaoPendente.id))]


@api.get("/silver/municipes")
def silver_municipes(tipo: str | None = None, limite: int = 50, offset: int = 0, db: Session = Depends(get_db)):
    """M7: documento sempre mascarado; empresas (J) não têm cidadao_id (M3)."""
    q = select(Municipe).order_by(Municipe.nome, Municipe.documento)
    if tipo:
        q = q.where(Municipe.tipo == tipo)
    return [{"documento": mascara_documento(m.documento), "tipo": m.tipo, "tipo_pessoa": m.tipo_pessoa, "nome": m.nome,
             "data_nascimento": m.data_nascimento, "data_alteracao": m.data_alteracao, "cidadao_id": m.cidadao_id,
             "confianca": m.confianca} for m in db.scalars(q.limit(min(limite, 200)).offset(offset))]


@api.get("/silver/municipes/pendencias")
def municipes_pendencias(u: Usuario = Depends(usuario_atual), db: Session = Depends(get_db)):
    """M10: perfil leitura recebe 403; operador/admin veem o documento só mascarado (a pendência guarda apenas
    a máscara; a chave é um hash)."""
    if u.perfil == "leitura":
        raise HTTPException(403, "Perfil leitura não acessa pendências de Munícipe")
    return [{"chave": p.chave, "motivo": p.motivo, "tipo_pessoa": p.tipo_pessoa, "nome": p.nome,
             "documento": p.documento_mascarado, "status": p.status}
            for p in db.scalars(select(MunicipePendencia).order_by(MunicipePendencia.chave))]


@api.get("/silver/inconsistencias")
def inconsistencias(db: Session = Depends(get_db)):
    return [{"id": i.id, "cidadao_id": i.cidadao_id, "sistema": i.sistema, "chave_origem": i.chave_origem, "tipo": i.tipo,
             "campo": i.campo, "detalhe": i.detalhe} for i in db.scalars(select(Inconsistencia).order_by(Inconsistencia.id))]


@api.get("/gold/cidadaos/{cid}")
def simulador(cid: str, db: Session = Depends(get_db)):
    c = db.get(CidadaoSistemas, cid) or _404()
    dados = list(db.scalars(select(DadoGold).where(DadoGold.cidadao_id == cid).order_by(DadoGold.dominio, DadoGold.campo)))
    ends = list(db.scalars(select(Endereco).where(Endereco.cidadao_id == cid).order_by(Endereco.fonte_atualizado_em.desc())))
    return {"cidadao_id": cid, "nome": c.nome, "cpf": mascara(_cpfs(db, [cid]).get(cid)), "sistemas": c.sistemas,
            "dominios": c.dominios,
            "dados": [{"dominio": d.dominio, "campo": d.campo, "valor": d.valor, "fonte": d.sistema,
                       "atualizado_em": d.fonte_atualizado_em} for d in dados],
            "enderecos": [{"valor": e.logradouro, "fonte": e.sistema, "atualizado_em": e.fonte_atualizado_em,
                           "mais_recente": e.mais_recente} for e in ends]}


@api.get("/gold/cidadaos/{cid}/sistemas")
def sistemas_do_cidadao(cid: str, db: Session = Depends(get_db)):
    c = db.get(CidadaoSistemas, cid) or _404()
    return {"cidadao_id": cid, "sistemas": c.sistemas, "dominios": c.dominios}


@api.post("/cidadaos/{cid}/revelar-cpf")
def revelar(cid: str, req: Request, dados: RevelarIn | None = None, u: Usuario = Depends(usuario_atual),
            db: Session = Depends(get_db)):
    if u.perfil not in get_settings().perfis_reveal:
        audita(db, u.email, "revelar_cpf_negado", f"cidadao:{cid}", ip=_ip(req))
        raise HTTPException(403, "Perfil sem permissão para revelar CPF")
    motivo = (dados.motivo or "").strip() if dados else ""
    if len(motivo) < get_settings().revelar_motivo_min:
        raise HTTPException(400, "Informe o motivo para revelar o CPF.")
    cpf = _cpfs(db, [cid]).get(cid)
    if cpf is None:
        _404()
    audita(db, u.email, "revelar_cpf", f"cidadao:{cid}", {"motivo": motivo}, _ip(req))
    return {"cidadao_id": cid, "cpf": cpf, "remascarar_em_segundos": get_settings().revelar_cpf_segundos}


MOTIVOS_OBITO = ("obito_revisao", "obito_ambiguo")
PERFIL_CONFIRMA_OBITO = "admin_lgpd"  # decisão do TL: fixo


@api.post("/silver/pendencias/{pid}/confirmar-obito")
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



