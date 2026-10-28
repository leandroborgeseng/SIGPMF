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

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.identity.cpf import normaliza, valido
from app.models.camadas import DividaAtiva, EmpresaSil, Identificador, Imovel, ImovelPossuidor, Lancamento, RegistroBronze

SISTEMAS = ("IPTU", "DIVIDA", "FINANCEIRO", "SIL")


def _ultimas(db: Session, sistema: str, tabela: str):
    sub = (select(func.max(RegistroBronze.id))
           .where(RegistroBronze.sistema_origem == sistema, RegistroBronze.tabela == tabela,
                  RegistroBronze.quarentena.is_(None))
           .group_by(RegistroBronze.id_origem))
    q = (select(RegistroBronze.id_origem, RegistroBronze.dados).where(RegistroBronze.id.in_(sub))
         .order_by(RegistroBronze.id).execution_options(yield_per=5000))
    yield from db.execute(q)


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


def reconstruir(db: Session) -> dict[str, int]:
    for m in (DividaAtiva, Lancamento, ImovelPossuidor, Imovel, EmpresaSil):
        db.execute(delete(m))
    cpf_cid = {v: c for v, c in db.execute(select(Identificador.valor, Identificador.cidadao_id)
                                            .where(Identificador.tipo == "cpf"))}

    imoveis: dict[str, Imovel] = {}
    for _io, d in _ultimas(db, "IPTU", "Cadastro"):
        codi = _s(d.get("CODI"), 22)
        if not codi:
            continue
        terr, cons = _n(d.get("Iptu_Terr_Liqu")), _n(d.get("Iptu_Cons_Liqu"))
        imoveis[codi] = Imovel(codi=codi, inscricao=_s(d.get("Insc_Imob"), 30), numero=_s(d.get("NR_CASE"), 20),
                               complemento=_s(d.get("COMPLE"), 200), cd_bairro=_i(d.get("Cd_Bai")),
                               quadra=_s(d.get("Quadra"), 200), lote=_s(d.get("Lote"), 200),
                               area_terreno=float(_n(d.get("STER")) or 0) or None,
                               iptu_anual=(terr or 0) + (cons or 0) if (terr is not None or cons is not None) else None,
                               situacao=_s(d.get("SITU"), 20), atualizado_em=_dt(d.get("Data_Alte")))
    n_imoveis = _grava(db, Imovel, imoveis.values())

    posses: list[ImovelPossuidor] = []
    principal: dict[str, tuple] = {}  # codi -> (desde, cidadao_id) do possuidor PF ativo mais recente com cidadão
    for _io, d in _ultimas(db, "IPTU", "Cadastro_Possuidor"):
        codi, doc = _s(d.get("CODI"), 22), _digitos(d.get("CPF_CNPJ"))
        if not codi or not doc:
            continue
        pf = len(doc) <= 11
        doc = doc.zfill(11) if pf else doc.zfill(14)
        cid = cpf_cid.get(normaliza(doc)) if pf and valido(doc) else None
        desde, ate = _dt(d.get("Data_Inclusao")), _dt(d.get("Data_Baixa"))
        p = ImovelPossuidor(codi=codi, documento=doc, tipo_pessoa="PF" if pf else "PJ", cidadao_id=cid,
                            tipo_posse=_i(d.get("Tipo_Posse")), desde=desde, ate=ate, ativo=ate is None)
        posses.append(p)
        if cid and ate is None:
            atual = principal.get(codi)
            chave = (desde or datetime.min, cid)
            if atual is None or chave[0] > atual[0]:
                principal[codi] = chave
    n_posses = _grava(db, ImovelPossuidor, posses)
    dono = {codi: cid for codi, (_d0, cid) in principal.items()}

    def lancamentos():
        for io, d in _ultimas(db, "FINANCEIRO", "Financeiro_Exercicio"):
            contrib = _s(d.get("Contribuinte"), 20)
            codi = contrib if contrib in imoveis else None
            pago_v = _n(d.get("Valor_Pago")) or Decimal(0)
            pagamento = _d(d.get("Data_Pagamento"))
            yield Lancamento(chave_origem=io[:200], exercicio=_i(d.get("Exercicio")) or 0, tributo=_s(d.get("Tributo"), 10),
                             receita=_s(d.get("Receita"), 10), parcela=_i(d.get("Parcela")), contribuinte=contrib,
                             codi=codi, cidadao_id=dono.get(codi) if codi else None, vencimento=_d(d.get("Data_Vencimento")),
                             valor=_n(d.get("Valor")), valor_pago=pago_v, pagamento=pagamento,
                             pago=pagamento is not None or pago_v > 0, status=_s(d.get("Status"), 50))
    n_lanc = _grava(db, Lancamento, lancamentos())

    def dividas():
        for io, d in _ultimas(db, "DIVIDA", "Divida"):
            codi = _s(d.get("CODI"), 22)
            if not codi:
                continue
            yield DividaAtiva(chave_origem=io[:200], codi=codi, ano=_i(d.get("ANO")), natureza=_i(d.get("Cod_Nat")),
                              cidadao_id=dono.get(codi), inscricao=_d(d.get("DT_INSCR")), vencimento=_d(d.get("DT_VENC")),
                              valor_origem=_n(d.get("VR_ORIGEM")), saldo=_n(d.get("VR_TOTA_DIVI")),
                              pago_total=_n(d.get("VR_PAGO_TOTAL")), status=_s(d.get("STATUS"), 5),
                              situacao=_i(d.get("SITUACAO")), protestos=_i(d.get("QTDE_PROTESTO")),
                              processo=_s(d.get("NR_PROC"), 30), extinta=_dt(d.get("Data_Extincao")) is not None)
    n_div = _grava(db, DividaAtiva, dividas())
    ligados = defaultdict(int)
    for p in posses:
        if p.cidadao_id:
            ligados["possuidores"] += 1

    def empresas():
        for _io, d in _ultimas(db, "SIL", "Empresas"):
            ccm = _i(d.get("Ccm"))
            if ccm is None:
                continue
            doc = _digitos(d.get("CNPJCPF")) or None
            pf = doc is not None and len(doc) <= 11
            if doc:
                doc = doc.zfill(11) if pf else doc.zfill(14)
            cid = cpf_cid.get(normaliza(doc)) if pf and valido(doc) else None
            if cid:
                ligados["empresas"] += 1
            deb = _s(d.get("PossuiDebito"), 1)
            yield EmpresaSil(ccm=ccm, documento=doc, tipo_pessoa=("PF" if pf else "PJ") if doc else None, cidadao_id=cid,
                             nome=_s(d.get("Nome"), 250), possui_debito=None if deb is None else deb.upper() == "S",
                             endereco=_s(d.get("Endereco"), 300), bairro=_s(d.get("Bairro"), 120), cep=_s(d.get("CEP"), 12))
    n_emp = _grava(db, EmpresaSil, empresas())
    return {"imoveis": n_imoveis, "possuidores": n_posses, "possuidores_com_cidadao": ligados["possuidores"],
            "lancamentos": n_lanc, "dividas": n_div, "empresas": n_emp, "empresas_com_cidadao": ligados["empresas"]}
