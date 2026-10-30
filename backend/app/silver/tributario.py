"""Prata tributária (onda 2, TR4–TR6): imóveis, possuidores, lançamentos e dívidas a partir da versão mais recente de
cada registro da bronze de IPTU, DIVIDA e FINANCEIRO. Determinística e idempotente (DELETE + INSERT), como a prata de
pessoas, e chamada no fim dela (os CPFs já estão correlacionados).

- Nunca cria cidadão: o vínculo com `cidadao_id` só existe quando o CPF do possuidor é um identificador já
  correlacionado (silver.identificadores). PJ e CPF sem cidadão ficam só com o documento (mascarado na API).
- Empresas da API SIL (por CCM): ligadas ao cidadão quando o CNPJ/CPF é um CPF correlacionado (MEI/autônomo).
- "Contribuinte" do Financeiro é polimórfico (inscrição imobiliária, inscrição do ISS, sepultura…): vira `codi` só
  quando é um CODI de imóvel conhecido; o cidadão do lançamento e da dívida é o possuidor PRINCIPAL ativo do imóvel
  (posse sem baixa, a mais recente).
"""
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from sqlalchemy import delete, func, insert, literal_column, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.identity.cpf import normaliza, valido
from app.models.camadas import DividaAtiva, EmpresaSil, Identificador, Imovel, ImovelPossuidor, Lancamento, RegistroBronze

SISTEMAS = ("IPTU", "DIVIDA", "FINANCEIRO", "SIL")
LOTE = 5000
# Parte 2: mesma expressão do índice bronze.ix_registros_iptu_codi (0038); o Python confere de novo com _s(CODI, 22)
CODI_SQL = literal_column("left(btrim(bronze.registros.dados->>'CODI'), 22)")


def _ultimas(db: Session, sistema: str, tabela: str, chaves=None, codis=None):
    """Última versão (produção, sem quarentena) de cada id_origem, na ordem de id. `chaves`/`codis` restringem às
    chaves ou aos CODIs pedidos (Parte 2, recálculo por chave)."""
    R = RegistroBronze
    w = [R.sistema_origem == sistema, R.tabela == tabela, R.quarentena.is_(None), R.ambiente_origem == "producao"]
    if chaves is not None:
        w.append(R.id_origem.in_(list(chaves)))
    if codis is not None:
        w.append(CODI_SQL.in_(list(codis)))
    # Parte 2: a ultima_versao decide QUAIS chaves/CODIs entram (o delta); a versão é lida aqui, pelo mesmo
    # predicado D6, via índice (CODI ou id_origem) + max(id). Juntar com a ultima_versao fazia o plano varrê-la a
    # cada lote (medido na bancada).
    sub = select(func.max(R.id)).where(*w).group_by(R.id_origem)
    q = select(R.id_origem, R.dados).where(R.id.in_(sub)).order_by(R.id).execution_options(yield_per=LOTE)
    if codis is None:
        yield from db.execute(q)
        return
    alvo = set(codis)
    for io, d in db.execute(q):
        if _s(d.get("CODI"), 22) in alvo:
            yield io, d


def _dt(v) -> datetime | None:
    if v in (None, ""):
        return None
    if isinstance(v, datetime):
        return v
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None


def _d(v) -> date | None:
    x = _dt(v)
    return x.date() if x else None


def _n(v) -> Decimal | None:
    if v in (None, ""):
        return None
    try:
        return Decimal(str(v))
    except (InvalidOperation, ValueError):
        return None


def _i(v) -> int | None:
    try:
        return None if v in (None, "") else int(float(v))
    except (TypeError, ValueError):
        return None


def _s(v, n: int) -> str | None:
    return None if v in (None, "") else str(v).strip()[:n] or None


def _digitos(v) -> str:
    return "".join(ch for ch in str(v or "") if ch.isdigit())


def _grava(db: Session, model, objs, lote: int = 5000) -> int:
    buf, n = [], 0
    for o in objs:
        buf.append(o)
        if len(buf) >= lote:
            db.add_all(buf)
            db.flush()
            n += len(buf)
            buf = []
    if buf:
        db.add_all(buf)
        db.flush()
        n += len(buf)
    return n


def _imovel(d) -> dict | None:
    codi = _s(d.get("CODI"), 22)
    if not codi:
        return None
    terr, cons = _n(d.get("Iptu_Terr_Liqu")), _n(d.get("Iptu_Cons_Liqu"))
    return {"codi": codi, "inscricao": _s(d.get("Insc_Imob"), 30), "numero": _s(d.get("NR_CASE"), 20),
            "complemento": _s(d.get("COMPLE"), 200), "cd_bairro": _i(d.get("Cd_Bai")), "quadra": _s(d.get("Quadra"), 200),
            "lote": _s(d.get("Lote"), 200), "area_terreno": float(_n(d.get("STER")) or 0) or None,
            "iptu_anual": (terr or 0) + (cons or 0) if (terr is not None or cons is not None) else None,
            "situacao": _s(d.get("SITU"), 20), "atualizado_em": _dt(d.get("Data_Alte"))}


def _posse(d, cid_de) -> dict | None:
    codi, doc = _s(d.get("CODI"), 22), _digitos(d.get("CPF_CNPJ"))
    if not codi or not doc:
        return None
    pf = len(doc) <= 11
    doc = doc.zfill(11) if pf else doc.zfill(14)
    cid = cid_de(normaliza(doc)) if pf and valido(doc) else None
    desde, ate = _dt(d.get("Data_Inclusao")), _dt(d.get("Data_Baixa"))
    return {"codi": codi, "documento": doc, "tipo_pessoa": "PF" if pf else "PJ", "cidadao_id": cid,
            "tipo_posse": _i(d.get("Tipo_Posse")), "desde": desde, "ate": ate, "ativo": ate is None}


def _principal(principal: dict, p: dict) -> None:
    """Possuidor PF ativo mais recente com cidadão (empate: o primeiro na ordem da bronze)."""
    if p["cidadao_id"] and p["ate"] is None:
        atual = principal.get(p["codi"])
        chave = (p["desde"] or datetime.min, p["cidadao_id"])
        if atual is None or chave[0] > atual[0]:
            principal[p["codi"]] = chave


def _lanc(io, d, eh_imovel, dono) -> dict:
    contrib = _s(d.get("Contribuinte"), 20)
    codi = contrib if eh_imovel(contrib) else None
    pago_v = _n(d.get("Valor_Pago")) or Decimal(0)
    pagamento = _d(d.get("Data_Pagamento"))
    return {"chave_origem": io[:200], "exercicio": _i(d.get("Exercicio")) or 0, "tributo": _s(d.get("Tributo"), 10),
            "receita": _s(d.get("Receita"), 10), "parcela": _i(d.get("Parcela")), "contribuinte": contrib, "codi": codi,
            "cidadao_id": dono.get(codi) if codi else None, "vencimento": _d(d.get("Data_Vencimento")),
            "valor": _n(d.get("Valor")), "valor_pago": pago_v, "pagamento": pagamento,
            "pago": pagamento is not None or pago_v > 0, "status": _s(d.get("Status"), 50)}


def _divida(io, d, dono) -> dict | None:
    codi = _s(d.get("CODI"), 22)
    if not codi:
        return None
    return {"chave_origem": io[:200], "codi": codi, "ano": _i(d.get("ANO")), "natureza": _i(d.get("Cod_Nat")),
            "cidadao_id": dono.get(codi), "inscricao": _d(d.get("DT_INSCR")), "vencimento": _d(d.get("DT_VENC")),
            "valor_origem": _n(d.get("VR_ORIGEM")), "saldo": _n(d.get("VR_TOTA_DIVI")),
            "pago_total": _n(d.get("VR_PAGO_TOTAL")), "status": _s(d.get("STATUS"), 5),
            "situacao": _i(d.get("SITUACAO")), "protestos": _i(d.get("QTDE_PROTESTO")),
            "processo": _s(d.get("NR_PROC"), 30), "extinta": _dt(d.get("Data_Extincao")) is not None}


def _empresa(d, cid_de) -> dict | None:
    ccm = _i(d.get("Ccm"))
    if ccm is None:
        return None
    doc = _digitos(d.get("CNPJCPF")) or None
    pf = doc is not None and len(doc) <= 11
    if doc:
        doc = doc.zfill(11) if pf else doc.zfill(14)
    cid = cid_de(normaliza(doc)) if pf and valido(doc) else None
    deb = _s(d.get("PossuiDebito"), 1)
    return {"ccm": ccm, "documento": doc, "tipo_pessoa": ("PF" if pf else "PJ") if doc else None, "cidadao_id": cid,
            "nome": _s(d.get("Nome"), 250), "possui_debito": None if deb is None else deb.upper() == "S",
            "endereco": _s(d.get("Endereco"), 300), "bairro": _s(d.get("Bairro"), 120), "cep": _s(d.get("CEP"), 12)}


def _insere(db: Session, model, linhas, pulso=None) -> int:
    """INSERT em lote (Core, sem identity map: memória constante). Parte 2: substitui o add_all, mesmo resultado."""
    buf, n = [], 0
    for x in linhas:
        if x is None:
            continue
        buf.append(x)
        if len(buf) >= LOTE:
            db.execute(insert(model), buf)
            n += len(buf)
            buf = []
            if pulso is not None:
                pulso.grava()
    if buf:
        db.execute(insert(model), buf)
        n += len(buf)
    return n


def _insere_imoveis(db: Session, linhas, pulso=None) -> int:
    """Mesmo CODI em duas chaves da bronze: vale a última na ordem de id (como o dict antigo). Upsert em lote."""
    buf: dict[str, dict] = {}
    t = Imovel.__table__

    def descarrega():
        st = pg_insert(t).values(list(buf.values()))
        st = st.on_conflict_do_update(index_elements=["codi"],
                                      set_={c.name: st.excluded[c.name] for c in t.columns if c.name != "codi"})
        db.execute(st)
        buf.clear()
    for x in linhas:
        if x is None:
            continue
        buf.pop(x["codi"], None)
        buf[x["codi"]] = x
        if len(buf) >= LOTE:
            descarrega()
            if pulso is not None:
                pulso.grava()
    if buf:
        descarrega()
    return int(db.scalar(select(func.count()).select_from(Imovel)) or 0)


def _cpf_cid_mapa(db: Session):
    m = {v: c for v, c in db.execute(select(Identificador.valor, Identificador.cidadao_id)
                                     .where(Identificador.tipo == "cpf"))}
    return m.get


def _cpf_cid_consulta(db: Session, docs: set[str]):
    if not docs:
        return lambda _x: None
    m = {v: c for v, c in db.execute(select(Identificador.valor, Identificador.cidadao_id)
                                     .where(Identificador.tipo == "cpf", Identificador.valor.in_(list(docs))))}
    return m.get


def _imoveis_existentes(db: Session, codis: set) -> set:
    codis = {c for c in codis if c}
    if not codis:
        return set()
    return set(db.scalars(select(Imovel.codi).where(Imovel.codi.in_(list(codis)))))


def _lancamentos(db: Session, linhas, dono, pulso=None) -> int:
    """Lançamentos em lotes; `contribuinte` vira `codi` só se for um imóvel conhecido (consulta por lote)."""
    n = 0
    lote: list = []

    def grava():
        nonlocal n
        imv = _imoveis_existentes(db, {_s(d.get("Contribuinte"), 20) for _io, d in lote})
        n += _insere(db, Lancamento, (_lanc(io, d, imv.__contains__, dono) for io, d in lote))
        lote.clear()
        if pulso is not None:
            pulso.grava()
    for x in linhas:
        lote.append(x)
        if len(lote) >= LOTE:
            grava()
    if lote:
        grava()
    return n


def reconstruir(db: Session, pulso=None) -> dict[str, int]:
    """Recomposição completa (DELETE + INSERT). Parte 2: em streaming e por INSERT em lote (memória constante), com
    o mesmo resultado da versão anterior (teste de equivalência e md5 nas evidências)."""
    for m in (DividaAtiva, Lancamento, ImovelPossuidor, Imovel, EmpresaSil):
        db.execute(delete(m))
    cid_de = _cpf_cid_mapa(db)
    n_imoveis = _insere_imoveis(db, (_imovel(d) for _io, d in _ultimas(db, "IPTU", "Cadastro")), pulso)
    principal: dict[str, tuple] = {}
    ligados = defaultdict(int)

    def posses():
        for _io, d in _ultimas(db, "IPTU", "Cadastro_Possuidor"):
            p = _posse(d, cid_de)
            if p is None:
                continue
            _principal(principal, p)
            if p["cidadao_id"]:
                ligados["possuidores"] += 1
            yield p
    n_posses = _insere(db, ImovelPossuidor, posses(), pulso)
    dono = {codi: cid for codi, (_d0, cid) in principal.items()}
    n_lanc = _lancamentos(db, _ultimas(db, "FINANCEIRO", "Financeiro_Exercicio"), dono, pulso)
    n_div = _insere(db, DividaAtiva, (_divida(io, d, dono) for io, d in _ultimas(db, "DIVIDA", "Divida")), pulso)

    def empresas():
        for _io, d in _ultimas(db, "SIL", "Empresas"):
            e = _empresa(d, cid_de)
            if e and e["cidadao_id"]:
                ligados["empresas"] += 1
            yield e
    n_emp = _insere(db, EmpresaSil, empresas(), pulso)
    return {"imoveis": n_imoveis, "possuidores": n_posses, "possuidores_com_cidadao": ligados["possuidores"],
            "lancamentos": n_lanc, "dividas": n_div, "empresas": n_emp, "empresas_com_cidadao": ligados["empresas"]}


# ---------- Parte 2: recálculo por chave (incremental) ----------
def _posses_de(db: Session, codis: set) -> list[dict]:
    """Posses dos CODIs a partir da bronze (ordem de id), com o cidadão do CPF já correlacionado."""
    linhas = [d for _io, d in _ultimas(db, "IPTU", "Cadastro_Possuidor", codis=codis)] if codis else []
    docs = set()
    for d in linhas:
        doc = _digitos(d.get("CPF_CNPJ"))
        if doc and len(doc) <= 11:
            docs.add(normaliza(doc.zfill(11)))
    cid_de = _cpf_cid_consulta(db, docs)
    return [p for p in (_posse(d, cid_de) for d in linhas) if p]


def _donos(db: Session, codis: set) -> dict:
    """Possuidor principal de cada CODI (mesma regra e desempate da recomposição: ordem de id da bronze)."""
    principal: dict[str, tuple] = {}
    for p in _posses_de(db, codis):
        _principal(principal, p)
    return {c: cid for c, (_d0, cid) in principal.items()}


def recalcula_codis(db: Session, codis: set) -> set:
    """Imóvel, posses, lançamentos (pelo contribuinte) e dívidas dos CODIs. Devolve os cidadãos afetados (antes e
    depois), para a linha do tempo."""
    codis = {c for c in codis if c}
    afetados: set = set()
    if not codis:
        return afetados
    L, D, P = Lancamento, DividaAtiva, ImovelPossuidor
    afetados |= set(db.scalars(select(P.cidadao_id).where(P.codi.in_(codis), P.cidadao_id.is_not(None))))
    db.execute(delete(Imovel).where(Imovel.codi.in_(codis)))
    db.execute(delete(P).where(P.codi.in_(codis)))
    _insere_imoveis(db, (_imovel(d) for _io, d in _ultimas(db, "IPTU", "Cadastro", codis=codis)))
    posses = _posses_de(db, codis)
    _insere(db, P, posses)
    afetados |= {p["cidadao_id"] for p in posses if p["cidadao_id"]}
    lancs = set(db.scalars(select(L.chave_origem).where(L.contribuinte.in_(codis))))
    divs = set(db.scalars(select(D.chave_origem).where(D.codi.in_(codis))))
    afetados |= recalcula_lancamentos(db, lancs) | recalcula_dividas(db, divs)
    return afetados


def recalcula_lancamentos(db: Session, chaves: set) -> set:
    L = Lancamento
    if not chaves:
        return set()
    afetados = set(db.scalars(select(L.cidadao_id).where(L.chave_origem.in_(chaves), L.cidadao_id.is_not(None))))
    db.execute(delete(L).where(L.chave_origem.in_(chaves)))
    linhas = list(_ultimas(db, "FINANCEIRO", "Financeiro_Exercicio", chaves=chaves))
    imv = _imoveis_existentes(db, {_s(d.get("Contribuinte"), 20) for _io, d in linhas})
    dono = _donos(db, imv)
    novos = [_lanc(io, d, imv.__contains__, dono) for io, d in linhas]
    _insere(db, L, novos)
    return afetados | {x["cidadao_id"] for x in novos if x["cidadao_id"]}


def recalcula_dividas(db: Session, chaves: set) -> set:
    D = DividaAtiva
    if not chaves:
        return set()
    afetados = set(db.scalars(select(D.cidadao_id).where(D.chave_origem.in_(chaves), D.cidadao_id.is_not(None))))
    db.execute(delete(D).where(D.chave_origem.in_(chaves)))
    linhas = list(_ultimas(db, "DIVIDA", "Divida", chaves=chaves))
    dono = _donos(db, {_s(d.get("CODI"), 22) for _io, d in linhas} - {None})
    novos = [x for x in (_divida(io, d, dono) for io, d in linhas) if x]
    _insere(db, D, novos)
    return afetados | {x["cidadao_id"] for x in novos if x["cidadao_id"]}


def recalcula_empresas(db: Session) -> set:
    """Empresas da SIL (fonte pequena): recomposição só desta tabela."""
    afetados = set(db.scalars(select(EmpresaSil.cidadao_id).where(EmpresaSil.cidadao_id.is_not(None))))
    db.execute(delete(EmpresaSil))
    cid_de = _cpf_cid_mapa(db)
    novos = [e for e in (_empresa(d, cid_de) for _io, d in _ultimas(db, "SIL", "Empresas")) if e]
    _insere(db, EmpresaSil, novos)
    return afetados | {e["cidadao_id"] for e in novos if e["cidadao_id"]}
