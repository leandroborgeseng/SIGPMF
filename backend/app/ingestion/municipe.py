"""Munícipe (ISS.Municipes): classificação pura das linhas lidas (aceite M1-M6).

O ISS não tem PK e o `CPF` é varchar(18) com formatação livre. Regras:
- M2: o documento é normalizado (só dígitos) antes de qualquer comparação; vazio = nulo.
- M1: deduplicação pelo documento normalizado; vale a linha de `Data_Alteracao` mais recente (empate: a última lida).
  As demais linhas do mesmo documento contam como duplicadas; as que divergem da vencedora ficam como versões.
- M3: `Tipo_Pessoa = 'J'` vira empresa, nunca cidadão.
- M4: `Tipo_Pessoa` 'R', nulo ou desconhecido -> pendência (até o DBA definir o significado).
- M2: `Documento_SoNumeros` (só dígitos, do ISS) confere a normalização: se diferir, vale o normalizado e a
  linha conta em `divergencia_documento` (sem expor o documento).
- M2: documento nulo ou com dígito verificador inválido -> pendência, sem `cidadao_id`.
- M5: 'F' com CPF válido -> cidadão por CPF (A13 v2: score 1,0, cidadao_id = uuid5 do CPF).
- M6: cada linha lida cai em exatamente uma categoria: nova, atualizada, inalterada, duplicada ou pendente.
Nenhum documento em claro sai daqui para log: a chave de pendência é um hash da linha.
"""
import hashlib
import json
from dataclasses import dataclass, field
from datetime import date, datetime

from app.identity import correlacao as corr
from app.identity import documento as docmod

CAMPOS = ("Tipo_Pessoa", "CPF", "Nome", "Nome_Mae", "Data_Nascimento", "Pis_Pasep", "Data_Alteracao", "Documento_SoNumeros")
CONFIANCA_CPF = 1.0


def _txt(v) -> str | None:
    if v is None:
        return None
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    s = str(v).strip()
    return s or None


def _quando(v) -> datetime:
    if isinstance(v, datetime):
        return v.replace(tzinfo=None)
    if isinstance(v, date):
        return datetime(v.year, v.month, v.day)
    try:
        return datetime.fromisoformat(str(v)).replace(tzinfo=None) if v else datetime.min
    except ValueError:
        return datetime.min


def canonico(linha: dict) -> dict:
    tipo = (_txt(linha.get("Tipo_Pessoa")) or "").upper() or None
    return {"tipo_pessoa": tipo, "documento": docmod.normaliza(_txt(linha.get("CPF"))),
            "nome": _txt(linha.get("Nome")), "nome_mae": _txt(linha.get("Nome_Mae")),
            "data_nascimento": _txt(linha.get("Data_Nascimento")), "pis_pasep": docmod.normaliza(_txt(linha.get("Pis_Pasep"))),
            "data_alteracao": _txt(linha.get("Data_Alteracao"))}


def diverge_documento(linha: dict) -> bool:
    """True se o ISS trouxe Documento_SoNumeros e ele difere do CPF normalizado (ausente = sem conferência)."""
    if "Documento_SoNumeros" not in linha:
        return False
    return docmod.normaliza(_txt(linha.get("Documento_SoNumeros"))) != docmod.normaliza(_txt(linha.get("CPF")))


def hash_conteudo(c: dict) -> str:
    """Hash dos campos de negócio (sem a data de alteração): versões iguais não são divergentes."""
    base = {k: v for k, v in c.items() if k != "data_alteracao"}
    return hashlib.sha256(json.dumps(base, sort_keys=True).encode()).hexdigest()


def chave_linha(c: dict, posicao: int) -> str:
    """Chave sintética de uma linha sem PK (não expõe o documento)."""
    return hashlib.sha256(json.dumps({**c, "_pos": posicao}, sort_keys=True).encode()).hexdigest()[:32]


@dataclass
class Registro:
    documento: str
    tipo: str                 # cidadao | empresa
    dados: dict
    hash: str
    cidadao_id: str | None
    confianca: float | None


@dataclass
class Resultado:
    registros: list[Registro] = field(default_factory=list)            # 1 por documento (novos + atualizados + inalterados)
    versoes: list[dict] = field(default_factory=list)                  # versões divergentes preservadas (M1)
    pendencias: list[dict] = field(default_factory=list)               # chave, motivo, nome
    contagem: dict = field(default_factory=lambda: dict.fromkeys(
        ("lidas", "novas", "atualizadas", "inalteradas", "duplicadas", "pendentes", "empresas", "cidadaos", "divergencia_documento"), 0))

    def fecha(self) -> bool:
        c = self.contagem
        return c["lidas"] == c["novas"] + c["atualizadas"] + c["inalteradas"] + c["duplicadas"] + c["pendentes"]


def classificar(linhas, existentes: dict[str, str] | None = None) -> Resultado:
    """`existentes`: documento normalizado -> hash já gravado na silver (para separar nova/atualizada/inalterada)."""
    existentes = existentes or {}
    r = Resultado()
    grupos: dict[str, list[tuple[int, dict]]] = {}
    for pos, linha in enumerate(linhas):
        r.contagem["lidas"] += 1
        c = canonico(linha)
        if diverge_documento(linha):
            r.contagem["divergencia_documento"] += 1   # informativo: não muda a categoria da linha (M6)
        if c["documento"] is None:
            r.contagem["pendentes"] += 1
            r.pendencias.append({"chave": chave_linha(c, pos), "motivo": "documento_nulo", "nome": c["nome"],
                                 "tipo_pessoa": c["tipo_pessoa"]})
            continue
        grupos.setdefault(c["documento"], []).append((pos, c))

    for doc, itens in grupos.items():
        ordenados = sorted(itens, key=lambda t: (_quando(t[1]["data_alteracao"]), t[0]))
        pos_v, venc = ordenados[-1]
        h = hash_conteudo(venc)
        divergentes = {}
        for _pos, c in ordenados[:-1]:
            r.contagem["duplicadas"] += 1
            hc = hash_conteudo(c)
            if hc != h and hc not in divergentes:
                divergentes[hc] = c
        tipo = venc["tipo_pessoa"]
        motivo = None
        if tipo not in ("F", "J"):
            motivo = "tipo_pessoa_indefinido"          # M4: R, nulo ou desconhecido
        elif not docmod.valido(doc, tipo):
            motivo = "documento_invalido"              # M2: DV inválido (ou tamanho incompatível com o tipo)
        if motivo:
            r.contagem["pendentes"] += 1
            r.pendencias.append({"chave": chave_linha(venc, pos_v), "motivo": motivo, "nome": venc["nome"],
                                 "tipo_pessoa": tipo})
            continue
        for hc, c in divergentes.items():
            r.versoes.append({"documento": doc, "hash": hc, "dados": c})
        if tipo == "J":
            reg = Registro(doc, "empresa", venc, h, None, None)
            r.contagem["empresas"] += 1
        else:
            reg = Registro(doc, "cidadao", venc, h, corr.cidadao_id_por_cpf(doc), CONFIANCA_CPF)
            r.contagem["cidadaos"] += 1
        r.registros.append(reg)
        anterior = existentes.get(doc)
        r.contagem["novas" if anterior is None else "atualizadas" if anterior != h else "inalteradas"] += 1
    return r
