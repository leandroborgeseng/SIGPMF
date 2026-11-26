"""Monta a árvore: até 2 níveis acima e 2 abaixo, sem ciclo e sem duplicar a mesma pessoa.

A ficha não varre a cidade. As arestas vêm de gold.genealogia_arestas (vizinhança indexada).
Nominais de propósito. ISENTO_K_ANONIMATO: esta resposta não passa por app.paineis.privacidade.
"""
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.genealogia.confianca import CRITERIO_FRACO
from app.genealogia.indice import Indice
from app.genealogia.leitura import ArvoreIndisponivel, Vizinhanca, carregar, eh_timeout
from app.genealogia.provedores import REGISTRO, Aresta
from app.identity.cpf import mascara
from app.models.camadas import (
    Cidadao,
    EmpresaSil,
    ImovelPossuidor,
    Vinculo,
)
from app.models.prata_modelo import CidadaoEmpresa

# Decisão escrita do dono do produto: árvore e folha nominais não aplicam k >= 5.
ISENTO_K_ANONIMATO = True
NIVEIS = 2
_GERACAO = ("ascendente", "descendente")


def _filtra(indice: Indice, arestas: list[Aresta]) -> list[Aresta]:
    """Forte de geração elimina a fraca do mesmo par. Pares repetidos na mesma base ficam com a maior confiança."""
    fortes = set()
    for a in arestas:
        if a.forca == "forte" and a.sentido in _GERACAO:
            par = (indice.find(a.de), indice.find(a.para))
            fortes.add(par)
            fortes.add((par[1], par[0]))
    vistas: dict[tuple, Aresta] = {}
    for a in arestas:
        de, para = indice.find(a.de), indice.find(a.para)
        if de == para:
            continue
        if a.forca == "fraco" and (de, para) in fortes:
            continue
        a.de, a.para = de, para
        k = (de, para, a.base, a.sentido, a.forca, a.parentesco)
        atual = vistas.get(k)
        if atual is None or a.confianca > atual.confianca:
            vistas[k] = a
    return list(vistas.values())


def _sobe(chave: str, arestas: list[Aresta]) -> list[tuple[str, Aresta]]:
    out = []
    for a in arestas:
        if a.sentido == "ascendente" and a.de == chave:
            out.append((a.para, a))
        elif a.sentido == "descendente" and a.para == chave:
            out.append((a.de, a))
    return out


def _desce(chave: str, arestas: list[Aresta]) -> list[tuple[str, Aresta]]:
    out = []
    for a in arestas:
        if a.sentido == "descendente" and a.de == chave:
            out.append((a.para, a))
        elif a.sentido == "ascendente" and a.para == chave:
            out.append((a.de, a))
    return out


def _anda(foco: str, arestas: list[Aresta], vizinhos, niveis: int) -> list[tuple[int, str, str | None, list[Aresta]]]:
    """BFS. O nó visitado não volta (corta ciclo). Cada pessoa aparece uma vez."""
    vistos = {foco}
    fila: list[tuple[str, int, str | None]] = [(foco, 0, None)]
    achados = []
    while fila:
        atual, nivel, via = fila.pop(0)
        if nivel >= niveis:
            continue
        por_prox: dict[str, list[Aresta]] = {}
        for prox, aresta in vizinhos(atual, arestas):
            por_prox.setdefault(prox, []).append(aresta)
        for prox, hops in por_prox.items():
            if prox in vistos:
                continue
            vistos.add(prox)
            achados.append((nivel + 1, prox, via, hops))
            fila.append((prox, nivel + 1, prox))
    return achados


def arvore_de(foco: str, indice: Indice, arestas: list[Aresta], niveis: int = NIVEIS) -> dict:
    """Árvore em memória (sem banco). `foco` é a chave já unificada."""
    foco = indice.find(foco)
    arestas = _filtra(indice, arestas)
    return {
        "foco": foco,
        "ascendentes": _anda(foco, arestas, _sobe, niveis),
        "descendentes": _anda(foco, arestas, _desce, niveis),
        "laterais": _laterais(foco, arestas),
        "arestas": arestas,
    }


def _laterais(foco: str, arestas: list[Aresta]) -> list[tuple[int, str, str | None, list[Aresta]]]:
    por: dict[str, list[Aresta]] = {}
    for a in arestas:
        if a.sentido != "conjuge":
            continue
        if a.de == foco:
            por.setdefault(a.para, []).append(a)
        elif a.para == foco:
            por.setdefault(a.de, []).append(a)
    return [(0, prox, None, hops) for prox, hops in por.items() if prox != foco]


def _iso(v):
    return v.isoformat() if v else None


def _indicadores(db: Session, indice: Indice, chaves: list[str]) -> dict[str, dict]:
    """Contagens só das pessoas da vizinhança. O CPF da SIL e do túmulo usa bronze.cpf_so_digitos, a expressão do índice."""
    nos = [(chave, indice.no(chave)) for chave in chaves]
    cids = list({no.cidadao_id for _, no in nos if no.cidadao_id})
    cpfs = list({no.cpf for _, no in nos if no.cpf})
    iptu_cid: set[str] = set()
    iptu_cpf: set[str] = set()
    sil_cpf: set[str] = set()
    tum_cid: set[str] = set()
    tum_cpf: set[str] = set()
    emp_cid: set[str] = set()
    emp_cpf: set[str] = set()
    iss: set[str] = set()
    if cids:
        iptu_cid = set(db.scalars(select(ImovelPossuidor.cidadao_id).where(ImovelPossuidor.cidadao_id.in_(cids))))
        tum_cid = set(db.scalars(select(Vinculo.cidadao_id).where(
            Vinculo.cidadao_id.in_(cids), Vinculo.sistema == "CEMITERIO")))
        emp_cid = set(db.scalars(select(EmpresaSil.cidadao_id).where(EmpresaSil.cidadao_id.in_(cids))))
        iss = set(db.scalars(select(CidadaoEmpresa.cidadao_id).where(CidadaoEmpresa.cidadao_id.in_(cids))))
    if cpfs:
        iptu_cpf = set(db.scalars(select(ImovelPossuidor.documento).where(ImovelPossuidor.documento.in_(cpfs))))
        emp_cpf = set(db.scalars(select(EmpresaSil.documento).where(EmpresaSil.documento.in_(cpfs))))
        sil_cpf = {r[0] for r in db.execute(text(
            "SELECT DISTINCT bronze.cpf_so_digitos(cpfcnpjresponsavel) FROM bronze.sil_imoveis_fisico "
            "WHERE bronze.cpf_so_digitos(cpfcnpjresponsavel) = ANY(:cpfs)"), {"cpfs": cpfs}) if r[0]}
        tum_cpf = {r[0] for r in db.execute(text(
            "SELECT DISTINCT bronze.cpf_so_digitos(dados->>'CPF') FROM bronze.registros "
            "WHERE sistema_origem = 'CEMITERIO' "
            "AND tabela IN ('Titulares', 'Leilao', 'Pessoas_Autorizadas') "
            "AND bronze.cpf_so_digitos(dados->>'CPF') = ANY(:cpfs)"), {"cpfs": cpfs}) if r[0]}

    def bloco(fontes, cid, ancora):
        return {"tem": bool(fontes), "fontes": fontes,
                "ficha": f"/simulador?cid={cid}#{ancora}" if cid and fontes else None}

    out = {}
    for chave, no in nos:
        cid, cpf = no.cidadao_id, no.cpf
        imovel, tumulo, empresa = [], [], []
        if cid and cid in iptu_cid:
            imovel.append("IPTU")
        if cpf and cpf in iptu_cpf and "IPTU" not in imovel:
            imovel.append("IPTU")
        if cpf and cpf in sil_cpf:
            imovel.append("SIL")
        if (cid and cid in tum_cid) or (cpf and cpf in tum_cpf):
            tumulo.append("CEMITERIO")
        if (cid and cid in emp_cid) or (cpf and cpf in emp_cpf):
            empresa.append("SIL")
        if cid and cid in iss:
            empresa.append("ISS")
        out[chave] = {"imovel": bloco(imovel, cid, "area-imoveis"), "tumulo": bloco(tumulo, cid, "area-tumulos"),
                      "empresa": bloco(empresa, cid, "area-empresas")}
    return out


def _pessoa(indice: Indice, chave: str, indicadores: dict) -> dict:
    no = indice.no(chave)
    return {"nome": no.nome, "cpf": mascara(no.cpf) if no.cpf else None,
            "cidadao_id": no.cidadao_id, "nascimento": _iso(no.nascimento),
            "indicadores": indicadores.get(chave) or {
                "imovel": {"tem": False, "fontes": [], "ficha": None},
                "tumulo": {"tem": False, "fontes": [], "ficha": None},
                "empresa": {"tem": False, "fontes": [], "ficha": None}}}


def _aresta_api(a: Aresta) -> dict:
    return {"base": a.base, "forca": a.forca, "parentesco": a.parentesco, "confianca": a.confianca,
            "criterio": a.criterio, "fonte_atualizado_em": _iso(a.fonte_atualizado_em),
            "provavel": a.forca == "fraco"}


def _lado(indice, itens, indicadores, nomes) -> list[dict]:
    saida = []
    for nivel, chave, via, hops in itens:
        saida.append({"nivel": nivel, "via": nomes.get(via) if via else None, "pessoa": _pessoa(indice, chave, indicadores),
                      "arestas": [_aresta_api(a) for a in hops]})
    return saida


def _indice_da_vizinhanca(db: Session, cidadao_id: str, viz: Vizinhanca) -> tuple[Indice, list[Aresta]]:
    indice = Indice()
    c = db.get(Cidadao, cidadao_id)
    indice.garante(nome=c.nome if c else None, cidadao_id=cidadao_id,
                   nascimento=c.data_nascimento if c else None, origem=f"CIDADAO:{cidadao_id}")
    for p in viz.pessoas:
        indice.garante(nome=p.nome, cpf=p.cpf, cidadao_id=p.cidadao_id, nascimento=p.nascimento,
                       origem=f"CIDADAO:{p.cidadao_id}")
    arestas = []
    for a in viz.arestas:
        if a.origem not in indice.por_cid or a.destino not in indice.por_cid:
            continue
        arestas.append(Aresta(
            de=indice.por_cid[a.origem], para=indice.por_cid[a.destino], sentido=a.sentido, base=a.base,
            forca=a.forca, parentesco=a.parentesco, confianca=a.confianca, criterio=a.criterio or "",
            fonte_atualizado_em=a.fonte_atualizado_em))
    return indice, arestas


def montar(db: Session, cidadao_id: str) -> dict:
    if not ISENTO_K_ANONIMATO:
        return {"foco": None, "ascendentes": [], "descendentes": [], "laterais": [], "provedores": [],
                "criterio_vinculo_fraco": CRITERIO_FRACO, "isento_k_anonimato": False}
    try:
        viz = carregar(db, cidadao_id)
        indice, brutas = _indice_da_vizinhanca(db, cidadao_id, viz)
        provedores = []
        for p in REGISTRO:
            ok = p.disponivel(db)
            provedores.append({"codigo": p.codigo, "disponivel": ok, "motivo": None if ok else p.motivo()})
        foco = indice.por_cid[cidadao_id]
        arv = arvore_de(foco, indice, brutas)
        chaves = [arv["foco"]] + [c for _n, c, _v, _h in arv["ascendentes"] + arv["descendentes"] + arv["laterais"]]
        ind = _indicadores(db, indice, chaves)
    except ArvoreIndisponivel:
        raise
    except Exception as exc:
        if eh_timeout(exc):
            db.rollback()
            from app.genealogia.leitura import MENSAGENS
            raise ArvoreIndisponivel("timeout", MENSAGENS["timeout"]) from exc
        raise
    nomes = {c: indice.no(c).nome for _n, c, _v, _h in arv["ascendentes"] + arv["descendentes"]}
    return {"foco": _pessoa(indice, arv["foco"], ind),
            "ascendentes": _lado(indice, arv["ascendentes"], ind, nomes),
            "descendentes": _lado(indice, arv["descendentes"], ind, nomes),
            "laterais": _lado(indice, arv["laterais"], ind, nomes),
            "provedores": provedores, "criterio_vinculo_fraco": CRITERIO_FRACO,
            "isento_k_anonimato": True}
