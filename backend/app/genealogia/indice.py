"""Pessoas da árvore. A mesma pessoa, com o mesmo CPF válido, é um único nó.

Nome igual não une nós: só o CPF. Sem CPF, a chave é o cidadão ou a origem (sistema + id).
"""
from dataclasses import dataclass
from datetime import date

from app.identity.correlacao import normaliza_nome
from app.identity.cpf import normaliza, valido


def cpf_valido(v) -> str | None:
    if isinstance(v, int) and not isinstance(v, bool):
        d = normaliza(v, origem_numerica=True)
    else:
        d = normaliza(None if v is None else str(v), origem_numerica=str(v).isdigit() if v is not None else False)
    if d and len(d) > 11:
        d = d[-11:]
    return d if d and valido(d) else None


@dataclass
class No:
    chave: str
    nome: str | None = None
    cpf: str | None = None
    cidadao_id: str | None = None
    nascimento: date | None = None
    cep: str | None = None
    logradouro: str | None = None


class Indice:
    def __init__(self):
        self.nos: dict[str, No] = {}
        self.pai: dict[str, str] = {}
        self.por_cpf: dict[str, str] = {}
        self.por_cid: dict[str, str] = {}
        self.por_origem: dict[str, str] = {}
        self.por_nome: dict[str, list[str]] = {}
        self.por_primeiro: dict[str, list[str]] = {}

    def find(self, chave: str) -> str:
        visto = chave
        while self.pai.get(visto, visto) != visto:
            self.pai[visto] = self.pai.get(self.pai[visto], self.pai[visto])
            visto = self.pai[visto]
        return visto

    def no(self, chave: str) -> No:
        return self.nos[self.find(chave)]

    def _indexa_nome(self, chave: str) -> None:
        nome = normaliza_nome(self.nos[chave].nome)
        if not nome:
            return
        self.por_nome.setdefault(nome, [])
        if chave not in self.por_nome[nome]:
            self.por_nome[nome].append(chave)
        tok = nome.split()[0]
        self.por_primeiro.setdefault(tok, [])
        if chave not in self.por_primeiro[tok]:
            self.por_primeiro[tok].append(chave)

    def _completa(self, destino: No, origem: No) -> None:
        if not destino.nome and origem.nome:
            destino.nome = origem.nome
            self._indexa_nome(destino.chave)
        for campo in ("cpf", "cidadao_id", "nascimento", "cep", "logradouro"):
            if getattr(destino, campo) is None and getattr(origem, campo) is not None:
                setattr(destino, campo, getattr(origem, campo))

    def unir(self, a: str, b: str) -> str:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return ra
        if self.nos[rb].cpf and not self.nos[ra].cpf:
            ra, rb = rb, ra
        self.pai[rb] = ra
        self._completa(self.nos[ra], self.nos[rb])
        return ra

    def garante(self, *, nome=None, cpf=None, cidadao_id=None, nascimento=None, cep=None, logradouro=None,
                origem: str) -> str:
        cpf = cpf_valido(cpf)
        alvos = []
        if cpf and cpf in self.por_cpf:
            alvos.append(self.por_cpf[cpf])
        if cidadao_id and cidadao_id in self.por_cid:
            alvos.append(self.por_cid[cidadao_id])
        if origem in self.por_origem:
            alvos.append(self.por_origem[origem])
        if not alvos:
            chave = f"cpf:{cpf}" if cpf else (f"cid:{cidadao_id}" if cidadao_id else f"origem:{origem}")
            if chave not in self.nos:
                self.nos[chave] = No(chave=chave, nome=nome, cpf=cpf, cidadao_id=cidadao_id,
                                     nascimento=nascimento, cep=cep, logradouro=logradouro)
                self.pai[chave] = chave
                self._indexa_nome(chave)
            alvos = [chave]
        raiz = alvos[0]
        for outro in alvos[1:]:
            raiz = self.unir(raiz, outro)
        no = self.nos[self.find(raiz)]
        extra = No(chave=no.chave, nome=nome, cpf=cpf, cidadao_id=cidadao_id, nascimento=nascimento,
                   cep=cep, logradouro=logradouro)
        self._completa(no, extra)
        raiz = self.find(raiz)
        if cpf:
            self.por_cpf[cpf] = raiz
        if cidadao_id:
            if cidadao_id in self.por_cid and self.find(self.por_cid[cidadao_id]) != raiz:
                raiz = self.unir(raiz, self.por_cid[cidadao_id])
            self.por_cid[cidadao_id] = raiz
        self.por_origem[origem] = raiz
        return raiz
