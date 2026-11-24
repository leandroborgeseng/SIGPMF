"""Prata e ouro nominais do Senior (r034fun e r036dep).

Decisão escrita do dono do produto: estes nominais não passam por k-anonimato
(app.paineis.privacidade, corte k >= 5). A ficha mostra a pessoa mesmo com um único registro.
ISENTO_K_ANONIMATO registra a decisão. Esta projeção não alimenta cubo agregado.

Grau de parentesco (R036DEP.grapar): quando a origem manda só o código, vale a lista padrão
do eSocial (tipo de dependente). Texto (filho, pai, mãe, cônjuge) prevalece. Código fora da
lista fica "grau N" e papel "outro": a aresta existe, mas não inventa geração.
"""
from datetime import UTC, date, datetime

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.bronze.ingestao import _id
from app.identity import correlacao as corr
from app.identity.cpf import normaliza, valido
from app.models.camadas import (
    Cidadao,
    DadoGold,
    Identificador,
    RegistroBronze,
    SeniorDependente,
    SeniorServidor,
    SistemaSilver,
    Vinculo,
)

SISTEMA = "SENIOR"
# Nominais de propósito: a ficha e a árvore não aplicam k >= 5.
ISENTO_K_ANONIMATO = True

# código eSocial / R036DEP.grapar -> (rótulo, papel na árvore)
GRAUS = {
    1: ("cônjuge", "conjuge"),
    2: ("companheiro(a)", "conjuge"),
    3: ("filho(a) ou enteado(a)", "descendente"),
    4: ("filho(a) ou enteado(a) estudante", "descendente"),
    6: ("irmão(ã), neto(a) ou bisneto(a)", "descendente"),
    9: ("pais, avós ou bisavós", "ascendente"),
    10: ("menor sob guarda", "descendente"),
    11: ("pessoa incapaz sob guarda", "outro"),
    12: ("ex-cônjuge", "conjuge"),
    99: ("agregado/outros", "outro"),
}


def classifica_grau(valor) -> tuple[str, str]:
    """(rótulo, papel) com papel em descendente, ascendente, conjuge ou outro."""
    if valor is None or valor == "":
        return "parentesco não informado", "outro"
    texto = str(valor).strip()
    if texto.isdigit():
        return GRAUS.get(int(texto), (f"grau {texto}", "outro"))
    n = corr.normaliza_nome(texto)
    if any(p in n for p in ("FILH", "ENTEAD", "NETO", "NETA", "BISNET")):
        return texto[:80], "descendente"
    if any(p in n for p in ("PAI", "MAE", "AVO", "BISAV", "GENITOR")):
        return texto[:80], "ascendente"
    if any(p in n for p in ("CONJUG", "COMPANH", "ESPOS", "MARID")):
        return texto[:80], "conjuge"
    return texto[:80], "outro"


def _ultimas(db: Session, tabela: str) -> list[tuple[int, dict]]:
    sub = (select(func.max(RegistroBronze.id)).where(RegistroBronze.sistema_origem == SISTEMA,
                                                     RegistroBronze.tabela == tabela,
                                                     RegistroBronze.quarentena.is_(None))
           .group_by(RegistroBronze.id_origem))
    return [(i, d) for i, d in db.execute(select(RegistroBronze.id, RegistroBronze.dados)
                                          .where(RegistroBronze.id.in_(sub)).order_by(RegistroBronze.id))]


def _int(v) -> int | None:
    try:
        return None if v is None or v == "" else int(v)
    except (TypeError, ValueError):
        return None


def _cpf(v) -> str | None:
    d = normaliza(v, origem_numerica=True) if not isinstance(v, str) else normaliza(v, origem_numerica=v.isdigit())
    if d and len(d) > 11:
        d = d[-11:]
    return d if d and valido(d) else None


def _dia(v) -> date | None:
    if not v:
        return None
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def _quando(v) -> datetime | None:
    if not v:
        return None
    try:
        dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def _cid(db: Session, cpf: str | None, nome: str | None, nasc, quando, tabela: str, chave: str) -> str:
    existente = None
    if cpf:
        existente = db.scalar(select(Identificador.cidadao_id).where(Identificador.tipo == "cpf",
                                                                     Identificador.valor == cpf))
        cid = existente or corr.cidadao_id_por_cpf(cpf)
    else:
        cid = corr.cidadao_id_por_origem(SISTEMA, tabela, chave)
    if db.get(Cidadao, cid) is None:
        db.add(Cidadao(cidadao_id=cid, nome=(nome or "Nome não informado")[:300], data_nascimento=nasc,
                       atualizado_em=quando))
        db.flush()
    elif nome and not db.get(Cidadao, cid).nome:
        db.get(Cidadao, cid).nome = nome[:300]
    if cpf and existente is None:
        db.add(Identificador(cidadao_id=cid, tipo="cpf", valor=cpf))
        db.flush()
    return cid


def _vinculo(db: Session, cid: str, tabela: str, chave: str, bronze_id: int, quando, com_cpf: bool) -> None:
    v = db.scalar(select(Vinculo).where(Vinculo.sistema == SISTEMA, Vinculo.tabela == tabela,
                                        Vinculo.chave_origem == chave))
    if v is None:
        db.add(Vinculo(cidadao_id=cid, sistema=SISTEMA, tabela=tabela, chave_origem=chave, bronze_id=bronze_id,
                       metodo="cpf" if com_cpf else "origem", confianca=1.0,
                       atributos=["cpf"] if com_cpf else [], fonte_atualizado_em=quando))
    else:
        v.cidadao_id, v.bronze_id = cid, bronze_id
        if quando is not None:
            v.fonte_atualizado_em = quando


def _upsert(db: Session, modelo, valores: dict, chave: tuple[str, ...], constraint: str) -> None:
    stmt = insert(modelo).values(**valores)
    muda = {k: getattr(stmt.excluded, k) for k in valores if k not in chave}
    db.execute(stmt.on_conflict_do_update(constraint=constraint, set_=muda))


def linhas_ouro(db: Session) -> list[dict]:
    """Uma linha nominal por cidadão (folha), com a data de procedência. Sem agregação e sem corte k."""
    if not ISENTO_K_ANONIMATO:
        return []
    por: dict[str, tuple[set[str], datetime | None]] = {}

    def poe(cid: str | None, rotulo: str, fonte) -> None:
        if not cid:
            return
        rotulos, atual = por.get(cid, (set(), None))
        rotulos.add(rotulo)
        if fonte is not None and (atual is None or fonte > atual):
            atual = fonte
        por[cid] = (rotulos, atual)

    for s in db.scalars(select(SeniorServidor)):
        poe(s.cidadao_id, "Servidor", s.fonte_atualizado_em)
    for d in db.scalars(select(SeniorDependente)):
        poe(d.cidadao_id, "Dependente", d.fonte_atualizado_em)
    return [{"cidadao_id": cid, "dominio": "rh", "campo": "folha_nominal",
             "valor": " · ".join(sorted(rotulos)), "sistema": SISTEMA,
             "fonte_atualizado_em": fonte, "mais_recente": True}
            for cid, (rotulos, fonte) in por.items()]


def projetar(db: Session) -> dict[str, int]:
    """Lê a bronze SENIOR e faz upsert da prata, do vínculo e da linha nominal do ouro. Não apaga outras pessoas."""
    if db.get(SistemaSilver, SISTEMA) is None:
        db.add(SistemaSilver(codigo=SISTEMA, nome="Senior — folha nominal e dependentes", dominio="rh"))
        db.flush()
    servidores: dict[tuple[int, int, int], dict] = {}
    n_srv = 0
    for bronze_id, d in _ultimas(db, "r034fun"):
        emp, tip, cad = _int(d.get("numemp")), _int(d.get("tipcol")), _int(d.get("numcad"))
        if None in (emp, tip, cad):
            continue
        chave = _id(("numemp", "tipcol", "numcad"), d)
        cpf, quando = _cpf(d.get("numcpf")), _quando(d.get("datafa")) or _quando(d.get("datadm"))
        cid = _cid(db, cpf, d.get("nomfun"), _dia(d.get("datnas")), quando, "r034fun", chave)
        _vinculo(db, cid, "r034fun", chave, bronze_id, quando, bool(cpf))
        valores = {"numemp": emp, "tipcol": tip, "numcad": cad, "nome": (d.get("nomfun") or None),
                   "cpf": cpf, "data_nascimento": _dia(d.get("datnas")), "data_admissao": _dia(d.get("datadm")),
                   "situacao": None if d.get("sitafa") is None else str(d.get("sitafa"))[:20],
                   "cidadao_id": cid, "fonte_atualizado_em": quando, "sistema": SISTEMA, "bronze_id": bronze_id}
        _upsert(db, SeniorServidor, valores, ("numemp", "tipcol", "numcad"), "uq_silver_senior_servidor")
        servidores[(emp, tip, cad)] = {"cpf": cpf, "cid": cid, "fonte": quando}
        n_srv += 1

    n_dep = 0
    for bronze_id, d in _ultimas(db, "r036dep"):
        emp, tip, cad, cod = _int(d.get("numemp")), _int(d.get("tipcol")), _int(d.get("numcad")), _int(d.get("coddep"))
        if None in (emp, tip, cad, cod):
            continue
        chave = _id(("numemp", "tipcol", "numcad", "coddep"), d)
        cpf = _cpf(d.get("numcpf"))
        titular = servidores.get((emp, tip, cad), {})
        quando = _quando(d.get("atualizado_em")) or titular.get("fonte")
        cid = _cid(db, cpf, d.get("nomdep"), _dia(d.get("datnas")), quando, "r036dep", chave)
        _vinculo(db, cid, "r036dep", chave, bronze_id, quando, bool(cpf))
        grau, papel = classifica_grau(d.get("grapar"))
        valores = {"numemp": emp, "tipcol": tip, "numcad": cad, "coddep": cod,
                   "nome": d.get("nomdep") or None, "cpf": cpf, "data_nascimento": _dia(d.get("datnas")),
                   "grapar": None if d.get("grapar") is None else str(d.get("grapar"))[:20],
                   "grau": grau, "papel": papel, "cpf_titular": titular.get("cpf"), "numcad_titular": cad,
                   "cidadao_id": cid, "cidadao_titular_id": titular.get("cid"),
                   "fonte_atualizado_em": quando, "sistema": SISTEMA, "bronze_id": bronze_id}
        _upsert(db, SeniorDependente, valores, ("numemp", "tipcol", "numcad", "coddep"), "uq_silver_senior_dependente")
        n_dep += 1
    db.flush()
    linhas = linhas_ouro(db)
    db.execute(delete(DadoGold).where(DadoGold.sistema == SISTEMA, DadoGold.campo == "folha_nominal"))
    if linhas:
        db.execute(insert(DadoGold), linhas)
    return {"servidores": n_srv, "dependentes": n_dep, "ouro": len(linhas)}
