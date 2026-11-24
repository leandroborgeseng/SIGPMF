"""Provedores de vínculo. CadÚnico e BU educação ficam registrados e desligados (sem acesso).

Para plugar um depois: implemente ProvedorVinculo (codigo, disponivel, motivo, arestas) e
acrescente a instância em REGISTRO. disponivel False faz a árvore listar o provedor sem aresta.
"""
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.bronze.ingestao import _id
from app.genealogia.confianca import LIMITE_SIMILARIDADE, Nota, pontua
from app.genealogia.indice import Indice, cpf_valido
from app.identity.correlacao import normaliza_nome
from app.models.camadas import (
    EscolaAluno,
    EscolaResponsavel,
    RegistroBronze,
    SeniorDependente,
    Vinculo,
)

# Tra_id 1 = mãe (mesma decisão da prata da educação). Pai entra pelo texto do tipo.
_TRA_MAE = 1


@dataclass
class Aresta:
    de: str
    para: str
    sentido: str  # ascendente | descendente | conjuge | outro
    base: str
    forca: str  # forte | fraco
    parentesco: str
    confianca: float
    criterio: str
    fonte_atualizado_em: datetime | None = None


class ProvedorVinculo:
    """Contrato de um provedor. disponivel False = sem acesso, nenhuma aresta."""

    codigo: str

    def disponivel(self, db: Session) -> bool:
        return True

    def motivo(self) -> str | None:
        return None

    def arestas(self, db: Session, indice: Indice) -> list[Aresta]:
        return []


class ProvedorIndisponivel(ProvedorVinculo):
    def __init__(self, codigo: str, motivo: str):
        self.codigo = codigo
        self._motivo = motivo

    def disponivel(self, db: Session) -> bool:
        return False

    def motivo(self) -> str | None:
        return self._motivo

    def arestas(self, db: Session, indice: Indice) -> list[Aresta]:
        return []


def _ultimas(db: Session, sistema: str, tabela: str) -> list[RegistroBronze]:
    sub = (select(func.max(RegistroBronze.id)).where(RegistroBronze.sistema_origem == sistema,
                                                     RegistroBronze.tabela == tabela,
                                                     RegistroBronze.quarentena.is_(None))
           .group_by(RegistroBronze.id_origem))
    return list(db.scalars(select(RegistroBronze).where(RegistroBronze.id.in_(sub))))


def _dia(v) -> date | None:
    if not v:
        return None
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def _casa(indice: Indice, texto: str, filho: str, nasc_filho, cep_filho, log_filho) -> tuple[str, Nota] | None:
    """Casa o texto (mãe/pai) com uma pessoa já conhecida. Nome sozinho não cria nó."""
    n = normaliza_nome(texto)
    if not n:
        return None
    filho_r = indice.find(filho)
    no_filho = indice.no(filho_r)

    def nota(chave: str, exigir_exato: bool) -> Nota | None:
        cand = indice.no(chave)
        if not cand.nome:
            return None
        r = pontua(texto, cand.nome, nasc_filho=nasc_filho or no_filho.nascimento, nasc_candidato=cand.nascimento,
                   cep_filho=cep_filho or no_filho.cep, log_filho=log_filho or no_filho.logradouro,
                   cep_candidato=cand.cep, log_candidato=cand.logradouro)
        if r is None or (exigir_exato and not r.exato) or (not exigir_exato and r.exato):
            return None
        return r

    exatos = list(dict.fromkeys(indice.find(k) for k in indice.por_nome.get(n, [])))
    exatos = [k for k in exatos if k != filho_r]
    if exatos:
        pontuados = [(k, nota(k, True)) for k in exatos]
        pontuados = [(k, r) for k, r in pontuados if r]
        if len(exatos) == 1 and pontuados:
            return pontuados[0]
        com_bonus = [(k, r) for k, r in pontuados if r.bonus]
        return com_bonus[0] if len(com_bonus) == 1 else None
    vistos, scored = [], []
    for k in indice.por_primeiro.get(n.split()[0], [])[:LIMITE_SIMILARIDADE]:
        fk = indice.find(k)
        if fk == filho_r or fk in vistos:
            continue
        vistos.append(fk)
        r = nota(fk, False)
        if r:
            scored.append((fk, r))
    if not scored:
        return None
    scored.sort(key=lambda x: -x[1].score)
    if len(scored) > 1 and scored[0][1].score == scored[1][1].score:
        return None
    return scored[0]


def _fraca(indice: Indice, filho: str, texto: str, parentesco: str, base: str, fonte, nasc, cep, log) -> Aresta | None:
    achado = _casa(indice, texto, filho, nasc, cep, log)
    if not achado:
        return None
    para, nota = achado
    return Aresta(de=indice.find(filho), para=para, sentido="ascendente", base=base, forca="fraco",
                  parentesco=parentesco, confianca=nota.score, criterio=nota.criterio, fonte_atualizado_em=fonte)


class ProvedorSenior(ProvedorVinculo):
    """r036dep: titular e dependente ligados pela folha. Confiança 1,0 (forte), com o grau da origem."""

    codigo = "SENIOR"

    def arestas(self, db: Session, indice: Indice) -> list[Aresta]:
        saida = []
        for d in db.scalars(select(SeniorDependente)):
            if not d.cidadao_id or not d.cidadao_titular_id:
                continue
            titular = indice.garante(nome=None, cpf=d.cpf_titular, cidadao_id=d.cidadao_titular_id,
                                     origem=f"SENIOR:titular:{d.numemp}:{d.tipcol}:{d.numcad}")
            dep = indice.garante(nome=d.nome, cpf=d.cpf, cidadao_id=d.cidadao_id, nascimento=d.data_nascimento,
                                 origem=f"SENIOR:dep:{d.numemp}:{d.tipcol}:{d.numcad}:{d.coddep}")
            if indice.find(titular) == indice.find(dep):
                continue
            _grau, papel = d.grau or "", d.papel or "outro"
            if papel not in ("ascendente", "descendente", "conjuge", "outro"):
                papel = "outro"
            saida.append(Aresta(
                de=titular, para=dep, sentido=papel, base="SENIOR", forca="forte",
                parentesco=d.grau or "parentesco", confianca=1.0,
                criterio="CPF do titular e do dependente na r036dep (numcad + coddep)",
                fonte_atualizado_em=d.fonte_atualizado_em))
        return saida


class ProvedorEducacao(ProvedorVinculo):
    """Filiação por pes_idFiliacaoMae / pes_idFiliacaoPai e responsável com CPF. Forte."""

    codigo = "EDUCACAO"

    def arestas(self, db: Session, indice: Indice) -> list[Aresta]:
        pessoas = {str(r.dados.get("pes_id")).lower(): r for r in _ultimas(db, "CORESSO", "PES_Pessoa")
                   if r.dados.get("pes_id")}
        docs: dict[str, str] = {}
        for r in _ultimas(db, "CORESSO", "PES_PessoaDocumento"):
            d = r.dados
            classe = d.get("tdo_classe") if d.get("tdo_classe") is not None else d.get("tdo_classificacao")
            try:
                classe = int(classe) if classe is not None and classe != "" else None
            except (TypeError, ValueError):
                classe = None
            cpf = cpf_valido(d.get("psd_numero"))
            if classe == 1 and cpf:
                docs[str(d.get("pes_id")).lower()] = cpf
        chaves = {}
        for pid, r in pessoas.items():
            d = r.dados
            chaves[pid] = indice.garante(
                nome=d.get("pes_nome"), cpf=docs.get(pid), nascimento=_dia(d.get("pes_dataNascimento")),
                origem=f"EDUCACAO:{pid}")
        saida = []
        for pid, r in pessoas.items():
            d = r.dados
            for campo, parentesco in (("pes_idFiliacaoMae", "mãe"), ("pes_idFiliacaoPai", "pai")):
                alvo = str(d.get(campo)).lower() if d.get(campo) else None
                if not alvo or alvo not in chaves or chaves[alvo] == chaves[pid]:
                    continue
                saida.append(Aresta(
                    de=chaves[pid], para=chaves[alvo], sentido="ascendente", base="EDUCACAO", forca="forte",
                    parentesco=parentesco, confianca=1.0, criterio=campo, fonte_atualizado_em=r.atualizado_origem_em))
        for resp in db.scalars(select(EscolaResponsavel).where(EscolaResponsavel.excluido.is_(False))):
            aluno = db.get(EscolaAluno, resp.alu_id)
            if aluno is None:
                continue
            filho = indice.garante(nome=aluno.nome, cpf=aluno.cpf, cidadao_id=aluno.cidadao_id,
                                   nascimento=aluno.data_nascimento, cep=aluno.cep, logradouro=aluno.logradouro,
                                   origem=f"EDUCACAO:aluno:{resp.alu_id}")
            pai = indice.garante(nome=resp.nome, cpf=resp.cpf, origem=f"EDUCACAO:resp:{resp.pes_id or resp.alr_id}")
            if indice.find(filho) == indice.find(pai):
                continue
            tipo = normaliza_nome(resp.tipo)
            if resp.tra_id == _TRA_MAE or any(p in tipo for p in ("MAE", "PAI", "FILIAC")):
                sentido, parentesco = "ascendente", (resp.tipo or "mãe/pai")
            else:
                sentido, parentesco = "outro", (resp.tipo or "responsável")
            saida.append(Aresta(
                de=filho, para=pai, sentido=sentido, base="EDUCACAO", forca="forte", parentesco=parentesco[:80],
                confianca=1.0, criterio="CPF do responsável (ACA_AlunoResponsavel)",
                fonte_atualizado_em=aluno.atualizado_em))
        return saida


class _ProvedorNome(ProvedorVinculo):
    """Base com nome de mãe/pai em texto: vínculo fraco, com a nota de confianca.pontua."""

    def __init__(self, codigo: str, sistema: str, tabela: str, nome: str, cpf: str, nasc: str,
                 mae: str, pai: str, cep: str | None = None, logradouro: str | None = None,
                 chave: str | tuple = "id"):
        self.codigo, self.sistema, self.tabela = codigo, sistema, tabela
        self.nome, self.cpf, self.nasc, self.mae, self.pai = nome, cpf, nasc, mae, pai
        self.cep, self.logradouro, self.chave = cep, logradouro, chave

    def arestas(self, db: Session, indice: Indice) -> list[Aresta]:
        saida = []
        for r in _ultimas(db, self.sistema, self.tabela):
            d = r.dados or {}
            cpf = cpf_valido(d.get(self.cpf)) if self.cpf else None
            try:
                chave = _id(self.chave, d)
            except KeyError:
                continue
            cid = None
            if not cpf:
                cid = db.scalar(select(Vinculo.cidadao_id).where(
                    Vinculo.sistema == self.sistema, Vinculo.tabela == self.tabela, Vinculo.chave_origem == chave))
            if not cpf and not cid:
                nome = normaliza_nome(d.get(self.nome))
                hits = list(dict.fromkeys(indice.find(k) for k in indice.por_nome.get(nome, []))) if nome else []
                if len(hits) != 1:
                    continue
                filho = hits[0]
            else:
                filho = indice.garante(nome=d.get(self.nome), cpf=cpf, cidadao_id=cid, nascimento=_dia(d.get(self.nasc)),
                                       cep=d.get(self.cep) if self.cep else None,
                                       logradouro=d.get(self.logradouro) if self.logradouro else None,
                                       origem=f"{self.sistema}:{self.tabela}:{chave}")
            for texto, parentesco in ((d.get(self.mae), "mãe"), (d.get(self.pai), "pai")):
                a = _fraca(indice, filho, texto, parentesco, self.codigo, r.atualizado_origem_em,
                           _dia(d.get(self.nasc)), d.get(self.cep) if self.cep else None,
                           d.get(self.logradouro) if self.logradouro else None)
                if a:
                    saida.append(a)
        return saida


class ProvedorMunicipe(ProvedorVinculo):
    """ISS.Municipes guarda o nome da mãe em texto. Fraco."""

    codigo = "ISS"

    def arestas(self, db: Session, indice: Indice) -> list[Aresta]:
        from app.models.camadas import Municipe
        saida = []
        for m in db.scalars(select(Municipe).where(Municipe.tipo == "cidadao")):
            if not m.nome_mae or not (m.cidadao_id or cpf_valido(m.documento)):
                continue
            filho = indice.garante(nome=m.nome, cpf=cpf_valido(m.documento), cidadao_id=m.cidadao_id,
                                   nascimento=m.data_nascimento, origem=f"ISS:municipes:{m.documento}")
            a = _fraca(indice, filho, m.nome_mae, "mãe", "ISS", m.data_alteracao, m.data_nascimento, None, None)
            if a:
                saida.append(a)
        return saida


# Ponto de extensão: novos provedores entram nesta lista. Os dois últimos não têm acesso.
REGISTRO: list[ProvedorVinculo] = [
    ProvedorSenior(),
    ProvedorEducacao(),
    _ProvedorNome("SAUDE", "SAUDE", "pacientes", "Nome", "Cpf", "Dt_Nasc", "Mae", "Pai", "Cep", "Outra_Rua", "Matricula"),
    _ProvedorNome("CEMITERIO", "CEMITERIO", "Obitos", "FALECIDO", "", "DT_NASC", "MAE", "PAI", chave=(
        "COD_CEMITERIO", "QUADRA", "SEPULTURA", "GAVETA", "LADO", "SEQUENCIA")),
    ProvedorMunicipe(),
    ProvedorIndisponivel("CADUNICO", "sem acesso"),
    ProvedorIndisponivel("BU_EDUCACAO", "sem acesso"),
]

__all__ = ["REGISTRO", "Aresta", "ProvedorVinculo", "ProvedorIndisponivel"]
