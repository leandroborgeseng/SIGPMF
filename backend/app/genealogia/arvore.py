"""Monta a árvore: até 2 níveis acima e 2 abaixo, sem ciclo e sem duplicar a mesma pessoa.

Nominais de propósito. ISENTO_K_ANONIMATO: esta resposta não passa por app.paineis.privacidade.
"""
from sqlalchemy import or_, select, text
from sqlalchemy.orm import Session

from app.genealogia.confianca import CRITERIO_FRACO
from app.genealogia.indice import Indice
from app.genealogia.provedores import REGISTRO, Aresta
from app.identity.cpf import mascara
from app.models.camadas import (
    Cidadao,
    EmpresaSil,
    Endereco,
    Identificador,
    ImovelPossuidor,
    Vinculo,
)
from app.models.prata_modelo import CidadaoEmpresa

# Decisão escrita do dono do produto: árvore e folha nominais não aplicam k >= 5.
ISENTO_K_ANONIMATO = True
NIVEIS = 2
_GERACAO = ("ascendente", "descendente")


def _carrega(db: Session, indice: Indice) -> None:
    cpfs = {i.cidadao_id: i.valor for i in db.scalars(select(Identificador).where(Identificador.tipo == "cpf"))}
    ends: dict[str, tuple] = {}
    for e in db.scalars(select(Endereco).order_by(Endereco.mais_recente.desc(), Endereco.id.desc())):
        ends.setdefault(e.cidadao_id, (e.cep, e.logradouro))
    for c in db.scalars(select(Cidadao)):
        cep, log = ends.get(c.cidadao_id, (None, None))
        indice.garante(nome=c.nome, cpf=cpfs.get(c.cidadao_id), cidadao_id=c.cidadao_id,
                       nascimento=c.data_nascimento, cep=cep, logradouro=log, origem=f"CIDADAO:{c.cidadao_id}")


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
    out = {}
    for chave in chaves:
        no = indice.no(chave)
        cid, cpf = no.cidadao_id, no.cpf
        imovel, tumulo, empresa = [], [], []
        if cid and db.scalar(select(ImovelPossuidor.id).where(ImovelPossuidor.cidadao_id == cid).limit(1)):
            imovel.append("IPTU")
        if cpf and db.scalar(select(ImovelPossuidor.id).where(ImovelPossuidor.documento == cpf).limit(1)):
            if "IPTU" not in imovel:
                imovel.append("IPTU")
        if cpf and db.execute(text(
                "SELECT 1 FROM bronze.sil_imoveis_fisico "
                "WHERE regexp_replace(COALESCE(cpfcnpjresponsavel, ''), '[^0-9]', '', 'g') = :cpf LIMIT 1"),
                {"cpf": cpf}).first():
            imovel.append("SIL")
        if cid and db.scalar(select(Vinculo.id).where(Vinculo.cidadao_id == cid, Vinculo.sistema == "CEMITERIO").limit(1)):
            tumulo.append("CEMITERIO")
        if cpf and db.execute(text(
                "SELECT 1 FROM bronze.registros WHERE sistema_origem = 'CEMITERIO' "
                "AND tabela IN ('Titulares', 'Leilao', 'Pessoas_Autorizadas') "
                "AND regexp_replace(COALESCE(dados->>'CPF', ''), '[^0-9]', '', 'g') = :cpf LIMIT 1"),
                {"cpf": cpf}).first():
            if "CEMITERIO" not in tumulo:
                tumulo.append("CEMITERIO")
        if cid or cpf:
            cond = []
            if cid:
                cond.append(EmpresaSil.cidadao_id == cid)
            if cpf:
                cond.append(EmpresaSil.documento == cpf)
            if db.scalar(select(EmpresaSil.ccm).where(or_(*cond)).limit(1)):
                empresa.append("SIL")
        if cid and db.scalar(select(CidadaoEmpresa.id).where(CidadaoEmpresa.cidadao_id == cid).limit(1)):
            empresa.append("ISS")
        def bloco(fontes, ancora):
            return {"tem": bool(fontes), "fontes": fontes,
                    "ficha": f"/simulador?cid={cid}#{ancora}" if cid and fontes else None}
        out[chave] = {"imovel": bloco(imovel, "area-imoveis"), "tumulo": bloco(tumulo, "area-tumulos"),
                      "empresa": bloco(empresa, "area-empresas")}
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


def montar(db: Session, cidadao_id: str) -> dict:
    if not ISENTO_K_ANONIMATO:
        return {"foco": None, "ascendentes": [], "descendentes": [], "laterais": [], "provedores": [],
                "criterio_vinculo_fraco": CRITERIO_FRACO, "isento_k_anonimato": False}
    indice = Indice()
    _carrega(db, indice)
    brutas: list[Aresta] = []
    provedores = []
    for p in REGISTRO:
        ok = p.disponivel(db)
        provedores.append({"codigo": p.codigo, "disponivel": ok, "motivo": None if ok else p.motivo()})
        if ok:
            brutas.extend(p.arestas(db, indice))
    foco = indice.por_cid.get(cidadao_id)
    if foco is None:
        c = db.get(Cidadao, cidadao_id)
        foco = indice.garante(nome=c.nome if c else None, cidadao_id=cidadao_id, origem=f"CIDADAO:{cidadao_id}")
    arv = arvore_de(foco, indice, brutas)
    chaves = [arv["foco"]] + [c for _n, c, _v, _h in arv["ascendentes"] + arv["descendentes"] + arv["laterais"]]
    ind = _indicadores(db, indice, chaves)
    nomes = {c: indice.no(c).nome for _n, c, _v, _h in arv["ascendentes"] + arv["descendentes"]}
    return {"foco": _pessoa(indice, arv["foco"], ind),
            "ascendentes": _lado(indice, arv["ascendentes"], ind, nomes),
            "descendentes": _lado(indice, arv["descendentes"], ind, nomes),
            "laterais": _lado(indice, arv["laterais"], ind, nomes),
            "provedores": provedores, "criterio_vinculo_fraco": CRITERIO_FRACO,
            "isento_k_anonimato": True}
