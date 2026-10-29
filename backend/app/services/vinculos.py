"""Explorador de vínculos (onda 6, VN1–VN4): vizinhos DIRETOS de um nó, um nível por pedido (consulta indexada).

Nós (ids opacos — nunca CPF/CNPJ no id):
- "pf:<cidadao_id>"  pessoa correlacionada (silver.cidadaos)
- "im:<codi>"        imóvel (silver.imoveis / imovel_possuidores)
- "ps:<id>"          possuidor sem cidadão (PJ ou CPF não correlacionado), pela linha de silver.imovel_possuidores;
                      o documento sai sempre mascarado
- "em:<ccm>"         empresa da API SIL (inscrição municipal)
Vínculos disponíveis hoje: pessoa ↔ imóvel (posse atual e anterior) ↔ outros possuidores; pessoa ↔ empresa (CPF do
titular na SIL); empresa ↔ imóveis possuídos pelo mesmo CNPJ/CPF. Sócios (ISS) e família (Senior/CadÚnico) entram como
novos tipos de aresta quando esses conectores forem ligados.
"""
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.identity.cpf import mascara
from app.identity.nome import mascara_nome
from app.models.camadas import Cidadao, DividaAtiva, EmpresaSil, Identificador, Imovel, ImovelPossuidor

NIVEL_MAXIMO = 3
POSSE = {1: "Proprietário(a)", 2: "Compromissário(a)", 3: "Possuidor(a)"}


class NoInvalido(ValueError):
    pass


def mascara_doc(doc: str | None) -> str:
    d = "".join(ch for ch in str(doc or "") if ch.isdigit())
    if len(d) == 14:
        return f"**.***.***/{d[8:12]}-**"
    return mascara(d) if d else "—"


def _divida(db: Session, *, cidadao: str | None = None, codi: str | None = None) -> float:
    q = select(func.coalesce(func.sum(DividaAtiva.saldo), 0)).where(DividaAtiva.extinta.is_(False))
    q = q.where(DividaAtiva.cidadao_id == cidadao) if cidadao else q.where(DividaAtiva.codi == codi)
    return float(db.scalar(q) or 0)


def _flags(divida: float, extra: list[str] | None = None) -> list[dict]:
    out = [{"texto": f"Dívida ativa R$ {divida:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."), "tom": "alerta"}] if divida > 0 else []
    return out + [{"texto": t, "tom": "neutro"} for t in (extra or [])]


def _rel_posse(p: ImovelPossuidor) -> str:
    papel = POSSE.get(p.tipo_posse or 0, "Possuidor(a)")
    return f"{papel} · {'desde ' + p.desde.strftime('%Y') if p.ativo and p.desde else 'posse anterior'}"


def _no_pessoa(db: Session, cid: str) -> dict:
    c = db.get(Cidadao, cid)
    if c is None:
        raise NoInvalido("Pessoa não encontrada")
    cpf = db.scalar(select(Identificador.valor).where(Identificador.cidadao_id == cid, Identificador.tipo == "cpf"))
    return {"id": f"pf:{cid}", "tipo": "PF", "nome": c.nome or "Nome não informado", "doc": mascara(cpf) if cpf else "sem CPF",
            "flags": _flags(_divida(db, cidadao=cid)), "cidadao_id": cid}


def _no_imovel(db: Session, codi: str) -> dict:
    i = db.get(Imovel, codi)
    if i is None and db.scalar(select(func.count()).select_from(ImovelPossuidor).where(ImovelPossuidor.codi == codi)) == 0:
        raise NoInvalido("Imóvel não encontrado")
    nome = (f"{i.complemento or 'Imóvel'}{', ' + i.numero if i and i.numero else ''}") if i else "Imóvel"
    return {"id": f"im:{codi}", "tipo": "IM", "nome": nome, "doc": f"Inscrição {(i.inscricao if i else None) or codi}",
            "flags": _flags(_divida(db, codi=codi)), "cidadao_id": None}


def _no_possuidor(db: Session, pid: int) -> tuple[dict, ImovelPossuidor]:
    p = db.get(ImovelPossuidor, pid)
    if p is None:
        raise NoInvalido("Possuidor não encontrado")
    if p.cidadao_id:
        return _no_pessoa(db, p.cidadao_id), p
    rotulo = "Empresa (CNPJ)" if p.tipo_pessoa == "PJ" else "Pessoa sem cadastro correlacionado"
    return {"id": f"ps:{p.id}", "tipo": p.tipo_pessoa, "nome": rotulo, "doc": mascara_doc(p.documento),
            "flags": _flags(0, ["Sem ficha no SIGPMF"]), "cidadao_id": None}, p


def _no_empresa(db: Session, ccm: int, revela: bool = False) -> dict:
    """SIL16/ALTO-1: razão social só em claro com revela (admin_lgpd); mascarada por padrão."""
    e = db.get(EmpresaSil, ccm)
    if e is None:
        raise NoInvalido("Empresa não encontrada")
    extra = ["Débito na SIL"] if e.possui_debito else []
    flags = [{"texto": t, "tom": "alerta"} for t in extra]
    return {"id": f"em:{ccm}", "tipo": "EM", "nome": (e.nome if revela else mascara_nome(e.nome)) or "Empresa sem nome",
            "doc": f"CCM {ccm} · {mascara_doc(e.documento)}", "flags": flags, "cidadao_id": None}


def _empresas_do_doc(db: Session, *, cidadao: str | None = None, documento: str | None = None,
                     revela: bool = False) -> list[dict]:
    q = select(EmpresaSil.ccm).order_by(EmpresaSil.ccm)
    q = q.where(EmpresaSil.cidadao_id == cidadao) if cidadao else q.where(EmpresaSil.documento == documento)
    return [_como_vizinho(_no_empresa(db, ccm, revela), f"Titular da inscrição municipal (CCM {ccm})", "SIL · empresa", "Empresas")
            for ccm in db.scalars(q.limit(100))]


def _como_vizinho(no: dict, relacao: str, fonte: str, grupo: str) -> dict:
    return {**no, "relacao": relacao, "fonte": fonte, "grupo": grupo}


def explorar(db: Session, no_id: str, *, revela_empresa: bool = False) -> dict:
    """Nó + vizinhos diretos agrupados (Imóveis, Possuidores). `revela_empresa` (só admin_lgpd) mostra a razão social."""
    tipo, _, chave = no_id.partition(":")
    vizinhos: list[dict] = []
    if tipo == "pf":
        no = _no_pessoa(db, chave)
        for p in db.scalars(select(ImovelPossuidor).where(ImovelPossuidor.cidadao_id == chave)
                            .order_by(ImovelPossuidor.ativo.desc(), ImovelPossuidor.desde.desc())):
            vizinhos.append(_como_vizinho(_no_imovel(db, p.codi), _rel_posse(p), "IPTU · possuidor", "Imóveis"))
        vizinhos += _empresas_do_doc(db, cidadao=chave, revela=revela_empresa)
    elif tipo == "em":
        try:
            ccm = int(chave)
        except ValueError:
            raise NoInvalido("Nó inválido") from None
        no = _no_empresa(db, ccm, revela_empresa)
        e = db.get(EmpresaSil, ccm)
        if e.cidadao_id:
            vizinhos.append(_como_vizinho(_no_pessoa(db, e.cidadao_id), "Titular (CPF)", "SIL · empresa", "Titular"))
        if e.documento:
            for p in db.scalars(select(ImovelPossuidor).where(ImovelPossuidor.documento == e.documento)
                                .order_by(ImovelPossuidor.ativo.desc(), ImovelPossuidor.desde.desc())):
                vizinhos.append(_como_vizinho(_no_imovel(db, p.codi), _rel_posse(p), "IPTU · possuidor", "Imóveis"))
    elif tipo == "im":
        no = _no_imovel(db, chave)
        vistos = set()
        for p in db.scalars(select(ImovelPossuidor).where(ImovelPossuidor.codi == chave)
                            .order_by(ImovelPossuidor.ativo.desc(), ImovelPossuidor.desde.desc())):
            viz, _p = _no_possuidor(db, p.id)
            if viz["id"] in vistos:
                continue
            vistos.add(viz["id"])
            vizinhos.append(_como_vizinho(viz, _rel_posse(p), "IPTU · possuidor", "Possuidores"))
    elif tipo == "ps":
        try:
            no, p0 = _no_possuidor(db, int(chave))
        except ValueError:
            raise NoInvalido("Nó inválido") from None
        for p in db.scalars(select(ImovelPossuidor).where(ImovelPossuidor.documento == p0.documento)
                            .order_by(ImovelPossuidor.ativo.desc(), ImovelPossuidor.desde.desc())):
            vizinhos.append(_como_vizinho(_no_imovel(db, p.codi), _rel_posse(p), "IPTU · possuidor", "Imóveis"))
        if p0.documento:
            vizinhos += _empresas_do_doc(db, documento=p0.documento, revela=revela_empresa)
    else:
        raise NoInvalido("Nó inválido")
    return {"no": no, "vizinhos": vizinhos}
