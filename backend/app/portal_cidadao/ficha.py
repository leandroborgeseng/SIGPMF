"""Ficha do portal: as mesmas áreas da consulta interna, só do cidadão da sessão.

A leitura reaproveita os serviços da ficha (cemitério, fontes, máscara da SIL, endereço do imóvel).
O id não entra na URL: quem chama passa o cidadao_id da sessão. JWT de servidor não chega aqui.

Árvore: lê gold.genealogia_arestas (geração de gold.genealogia_estado). Não usa
app.genealogia.arvore.montar. Tabela ausente, geração ainda não gravada ou tempo esgotado
viram estado "indisponivel", sem exceção para a página.
"""
from datetime import date, datetime

from sqlalchemy import Boolean, Column, DateTime, Integer, MetaData, String, Table, and_, func, or_, select, text
from sqlalchemy.orm import Session

from app.identity.cns import mascara as mascara_cns
from app.identity.nome import mascara_nome, mascara_texto
from app.models.camadas import (
    Agendamento,
    Atendimento,
    DadoGold,
    DividaAtiva,
    EmpresaSil,
    Endereco,
    EscolaAluno,
    EscolaMatricula,
    Evento,
    Identificador,
    Imovel,
    ImovelPossuidor,
    Lancamento,
    UnidadeSaude,
    Vacinacao,
)
from app.services.vinculos import mascara_doc

# Fora do metadata do Alembic: a tabela nasce no PR da árvore. Aqui só se lê.
_META = MetaData()
ARESTAS = Table(
    "genealogia_arestas", _META,
    Column("origem", String),
    Column("destino", String),
    Column("sentido", String),
    Column("parentesco", String),
    Column("ativo", Boolean),
    Column("geracao", Integer),
    schema="gold",
)
ESTADO = Table(
    "genealogia_estado", _META,
    Column("id", Integer),
    Column("geracao", Integer),
    Column("populado_em", DateTime(timezone=True)),
    schema="gold",
)

_CAMPOS_CADASTRO = ("nome_mae", "nome_pai", "sexo")
_INVERSO = {"mãe": "filho ou filha", "pai": "filho ou filha", "mãe/pai": "filho ou filha", "responsável": "dependente"}
_CHAVES_FONTE = ("vacinas", "consultas", "agendamentos", "imoveis", "lancamentos", "dividas", "empresas", "educacao", "cemiterio")


def _salvo(db: Session, fn, default):
    """Leitura opcional. Tabela ou função ausente desfaz só o savepoint e devolve o vazio."""
    try:
        with db.begin_nested():
            return fn()
    except Exception:
        return default


def _campo(d: DadoGold | None) -> dict:
    if d is None:
        return {"valor": None, "fonte": None, "atualizado_em": None}
    return {"valor": d.valor, "fonte": d.sistema, "atualizado_em": d.fonte_atualizado_em}


def cadastro(db: Session, cid: str) -> dict:
    """Recorte do cadastro: mãe, pai, sexo, CNS mascarado e endereço. Sem diagnóstico."""
    dados = {d.campo: d for d in db.scalars(select(DadoGold).where(
        DadoGold.cidadao_id == cid, DadoGold.mais_recente.is_(True), DadoGold.campo.in_(_CAMPOS_CADASTRO)))}
    cns = db.scalar(select(Identificador.valor).where(
        Identificador.cidadao_id == cid, Identificador.tipo == "cns").limit(1))
    ends = list(db.scalars(select(Endereco).where(Endereco.cidadao_id == cid)
                           .order_by(Endereco.mais_recente.desc(), Endereco.id.desc())))
    enderecos = [{"valor": e.logradouro, "fonte": e.sistema, "atualizado_em": e.fonte_atualizado_em,
                  "mais_recente": bool(e.mais_recente)} for e in ends]
    if not enderecos:
        extra = db.scalar(select(DadoGold).where(
            DadoGold.cidadao_id == cid, DadoGold.mais_recente.is_(True), DadoGold.campo == "endereco"))
        if extra and extra.valor:
            enderecos = [{"valor": extra.valor, "fonte": extra.sistema, "atualizado_em": extra.fonte_atualizado_em,
                          "mais_recente": True}]
    return {
        "nome_mae": _campo(dados.get("nome_mae")),
        "nome_pai": _campo(dados.get("nome_pai")),
        "sexo": _campo(dados.get("sexo")),
        "cns": mascara_cns(cns),
        "enderecos": enderecos,
    }


def _naturezas(db: Session, codigos: set[int]) -> dict[int, str]:
    if not codigos:
        return {}
    from sqlalchemy import bindparam
    q = text("SELECT codigo, descricao FROM ref.divida_natureza WHERE codigo IN :c").bindparams(
        bindparam("c", expanding=True))

    def ler():
        return {c: d for c, d in db.execute(q, {"c": sorted(codigos)}).all()}

    return _salvo(db, ler, {})


def _origem_divida(natureza_nome: str | None, imovel: str | None) -> str | None:
    if natureza_nome and imovel:
        return f"{natureza_nome} — imóvel {imovel}"
    if imovel:
        return f"Imóvel {imovel}"
    return natureza_nome or None


def _empresa(e: EmpresaSil) -> dict:
    """Mesma máscara da ficha interna. O portal nunca revela razão social, endereço ou bairro."""
    return {"ccm": e.ccm, "nome": mascara_nome(e.nome), "documento": mascara_doc(e.documento),
            "possui_debito": e.possui_debito, "endereco": mascara_texto(e.endereco, False),
            "bairro": mascara_texto(e.bairro, False)}


def tributario(db: Session, cid: str) -> dict:
    """Imóveis atuais e históricos, lançamentos, dívida ativa e empresas SIL do próprio cidadão."""
    from app.silver.endereco_imovel import endereco_do_imovel, enderecos_por_inscricoes

    empresas = list(db.scalars(select(EmpresaSil).where(EmpresaSil.cidadao_id == cid).order_by(EmpresaSil.ccm)))
    posses = list(db.scalars(select(ImovelPossuidor).where(ImovelPossuidor.cidadao_id == cid)
                             .order_by(ImovelPossuidor.ativo.desc(), ImovelPossuidor.desde.desc())))
    imoveis = {i.codi: i for i in db.scalars(select(Imovel).where(Imovel.codi.in_([p.codi for p in posses])))} if posses else {}
    chaves = [p.codi for p in posses] + [i.inscricao for i in imoveis.values() if i.inscricao]
    enderecos = _salvo(db, lambda: enderecos_por_inscricoes(db, chaves), {})
    lancs = list(db.scalars(select(Lancamento).where(Lancamento.cidadao_id == cid)
                            .order_by(Lancamento.vencimento.desc().nulls_last()).limit(200)))
    divs = list(db.scalars(select(DividaAtiva).where(DividaAtiva.cidadao_id == cid).order_by(DividaAtiva.ano.desc())))
    hoje = date.today()
    f = lambda v: None if v is None else float(v)  # noqa: E731
    naturezas = _naturezas(db, {d.natureza for d in divs if d.natureza is not None})
    insc_div = dict(db.execute(select(Imovel.codi, Imovel.inscricao).where(
        Imovel.codi.in_({d.codi for d in divs}))).all()) if divs else {}
    em_aberto = db.execute(select(Lancamento.valor, Lancamento.vencimento).where(
        Lancamento.cidadao_id == cid, Lancamento.pago.is_(False))).all()
    total_lancs = db.scalar(select(func.count()).select_from(Lancamento).where(Lancamento.cidadao_id == cid)) or 0
    vencido = [x for x in em_aberto if x.vencimento and x.vencimento < hoje]
    ativa = [d for d in divs if d.em_aberto]
    return {
        "totais": {"imoveis": len({p.codi for p in posses if p.ativo}),
                   "em_aberto": round(sum(f(x.valor) or 0 for x in em_aberto), 2), "parcelas_em_aberto": len(em_aberto),
                   "vencido": round(sum(f(x.valor) or 0 for x in vencido), 2), "parcelas_vencidas": len(vencido),
                   "divida_ativa": round(sum(f(d.saldo) or 0 for d in ativa), 2), "inscricoes_divida": len(ativa),
                   "protestada": any((d.protestos or 0) > 0 for d in ativa), "empresas": len(empresas),
                   "lancamentos": total_lancs},
        "imoveis": [{"codi": p.codi, "inscricao": getattr(imoveis.get(p.codi), "inscricao", None),
                     "numero": getattr(imoveis.get(p.codi), "numero", None),
                     "complemento": getattr(imoveis.get(p.codi), "complemento", None),
                     "endereco": endereco_do_imovel(enderecos, p.codi, getattr(imoveis.get(p.codi), "inscricao", None)),
                     "desde": p.desde, "ate": p.ate, "ativo": bool(p.ativo)} for p in posses],
        "lancamentos": [{"exercicio": x.exercicio, "tributo": x.tributo, "parcela": x.parcela,
                         "vencimento": x.vencimento, "valor": f(x.valor), "pago": bool(x.pago),
                         "situacao": "pago" if x.pago else ("vencido" if x.vencimento and x.vencimento < hoje else "a_vencer")}
                        for x in lancs],
        "dividas": [{"ano": d.ano, "inscrita_em": d.inscricao, "saldo": f(d.saldo), "em_aberto": bool(d.em_aberto),
                     "extinta": bool(d.extinta), "protestos": d.protestos or 0,
                     "origem": _origem_divida(naturezas.get(d.natureza), insc_div.get(d.codi) or d.codi)}
                    for d in divs],
        "empresas": [_empresa(e) for e in empresas],
    }


def saude(db: Session, cid: str) -> dict:
    """Vacinas, consultas e agendamentos. Sem diagnóstico e sem anotação clínica."""
    from app.silver.fatos import nomes, rotulo_especialidade

    uni = _salvo(db, lambda: {x.codigo: x.nome for x in db.scalars(select(UnidadeSaude))}, {})
    esp = _salvo(db, lambda: nomes(db)["especialidades"], {})
    hoje = date.today()
    ag = list(db.scalars(select(Agendamento).where(Agendamento.cidadao_id == cid, Agendamento.data_consulta >= hoje)
                         .order_by(Agendamento.data_consulta).limit(50)))
    at = list(db.scalars(select(Atendimento).where(Atendimento.cidadao_id == cid)
                         .order_by(Atendimento.data.desc().nulls_last()).limit(200)))
    va = list(db.scalars(select(Vacinacao).where(Vacinacao.cidadao_id == cid)
                         .order_by(Vacinacao.data.desc().nulls_last()).limit(200)))
    conta = lambda m: db.scalar(select(func.count()).select_from(m).where(m.cidadao_id == cid)) or 0  # noqa: E731
    return {
        "totais": {"atendimentos": conta(Atendimento), "vacinas": conta(Vacinacao)},
        "proximos": [{"data": a.data_consulta, "unidade": uni.get(a.unidade),
                      "especialidade": rotulo_especialidade(a.especialidade, esp), "dias_espera": a.dias_espera} for a in ag],
        "atendimentos": [{"data": a.data, "unidade": uni.get(a.unidade),
                          "especialidade": rotulo_especialidade(a.especialidade, esp)} for a in at],
        "vacinas": [{"data": v.data, "vacina": v.vacina, "dose": v.dose, "unidade": uni.get(v.unidade)} for v in va],
    }


def _matricula(m: EscolaMatricula) -> dict:
    return {"escola": m.escola, "unidade": m.unidade, "turma": m.turma, "inativa": not m.atual,
            "data_alteracao": m.data_alteracao.date().isoformat() if isinstance(m.data_alteracao, datetime)
            else (m.data_alteracao.isoformat() if m.data_alteracao else None)}


def educacao(db: Session, cid: str) -> dict:
    """Só as matrículas do autenticado. Sem CPF, endereço ou responsável de outra pessoa."""
    alunos = list(db.scalars(select(EscolaAluno).where(
        EscolaAluno.cidadao_id == cid, EscolaAluno.status_vinculo == "vinculado", EscolaAluno.excluido.is_(False))
        .order_by(EscolaAluno.alu_id)))
    saida = []
    for a in alunos:
        mats = list(db.scalars(select(EscolaMatricula).where(
            EscolaMatricula.alu_id == a.alu_id, EscolaMatricula.excluida.is_(False))
            .order_by(EscolaMatricula.mtu_id.desc())))
        saida.append({"inativo": a.situacao != 1,
                      "matricula_atual": next((_matricula(x) for x in mats if x.atual), None),
                      "historico": [_matricula(x) for x in mats]})
    return {"matriculas": saida}


def cemiterio(db: Session, cid: str) -> dict:
    """Vínculos da própria pessoa. Não lista os outros titulares da sepultura."""
    from app.services import ficha_cidadao
    vazio = {"titular": [], "autorizado": [], "leilao": [], "sepultamento": []}
    return _salvo(db, lambda: ficha_cidadao.cemiterio(db, cid), vazio)


def fontes(db: Session) -> dict:
    from app.services import ficha_cidadao
    vazio = {"carregadas": {k: False for k in _CHAVES_FONTE},
             "nao_integradas": [a for a, _b in ficha_cidadao.NAO_INTEGRADAS]}
    return _salvo(db, lambda: ficha_cidadao.fontes(db), vazio)


def linha(db: Session, cid: str, limite: int = 50, antes: str | None = None) -> dict:
    """Eventos do próprio cidadão. SEI não entra no portal."""
    q = select(Evento).where(Evento.cidadao_id == cid)
    if antes:
        q = q.where(Evento.data < date.fromisoformat(antes))
    teto = max(1, min(limite, 200))
    eventos = []
    for e in db.scalars(q.order_by(Evento.data.desc(), Evento.id.desc()).limit(teto)):
        if (e.area or "").lower() == "sei" or (e.fonte or "").upper() == "SEI":
            continue
        eventos.append({"data": e.data, "area": e.area, "tipo": e.tipo, "titulo": e.titulo, "fonte": e.fonte})
    return {"eventos": eventos}


def _vinculo(eu: str, origem: str, sentido: str, parentesco: str) -> str:
    p = (parentesco or "").strip() or "parente"
    if origem == eu:
        return p
    if sentido == "ascendente":
        return _INVERSO.get(p, "descendente")
    if sentido == "conjuge":
        return p
    if sentido == "descendente":
        return "ascendente" if p in _INVERSO else p
    return p


def _nome(db: Session, cid: str) -> str:
    from app.models.camadas import Cidadao
    c = db.get(Cidadao, cid)
    return (c.nome or "").strip() or "Nome não informado" if c else "Nome não informado"


def _genealogia(db: Session, cid: str) -> dict:
    if db.bind.dialect.name == "postgresql":
        _salvo(db, lambda: db.execute(text("SET LOCAL statement_timeout = '10s'")), None)
    row = db.execute(select(ESTADO.c.geracao, ESTADO.c.populado_em).where(ESTADO.c.id == 1)).first()
    if row is None or row.populado_em is None:
        return {"estado": "indisponivel", "parentes": []}
    geracao = int(row.geracao)
    vistos = {cid}
    fronteira = [cid]
    parentes: list[dict] = []
    for _nivel in range(2):
        if not fronteira or len(parentes) >= 80:
            break
        cond = and_(
            ARESTAS.c.geracao == geracao,
            or_(ARESTAS.c.ativo.is_(True), ARESTAS.c.ativo.is_(None)),
            or_(ARESTAS.c.origem.in_(fronteira), ARESTAS.c.destino.in_(fronteira)),
        )
        linhas = db.execute(select(ARESTAS.c.origem, ARESTAS.c.destino, ARESTAS.c.sentido, ARESTAS.c.parentesco).where(cond)).all()
        proxima: list[str] = []
        marca = set(fronteira)
        for origem, destino, sentido, parentesco in linhas:
            if origem in marca and destino not in vistos:
                outro, conhecido = destino, origem
            elif destino in marca and origem not in vistos:
                outro, conhecido = origem, destino
            else:
                continue
            vistos.add(outro)
            proxima.append(outro)
            parentes.append({"nome": _nome(db, outro),
                             "parentesco": _vinculo(conhecido, origem, sentido or "", parentesco or "")})
            if len(parentes) >= 80:
                break
        fronteira = proxima
    if not parentes:
        return {"estado": "vazio", "parentes": []}
    return {"estado": "ok", "parentes": parentes}


def genealogia(db: Session, cid: str) -> dict:
    """Vizinhança em gold.genealogia_arestas. Só nome e parentesco saem daqui."""
    try:
        with db.begin_nested():
            bruto = _genealogia(db, cid)
    except Exception:
        return {"estado": "indisponivel", "parentes": []}
    parentes = []
    for p in bruto.get("parentes") or []:
        nome = str(p.get("nome") or "").strip() or "Nome não informado"
        vinculo = str(p.get("parentesco") or "").strip() or "parente"
        parentes.append({"nome": nome, "parentesco": vinculo})
    estado = bruto.get("estado") if bruto.get("estado") in ("ok", "vazio", "indisponivel") else "indisponivel"
    if estado == "ok" and not parentes:
        estado = "vazio"
    return {"estado": estado, "parentes": parentes if estado == "ok" else []}
