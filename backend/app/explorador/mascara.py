"""Máscara de dados pessoais do explorador (EX1/EX2).

Regra: pelo NOME da coluna (cpf, cnpj, cns, documento, nome, mãe, endereço, telefone…) e, por segurança, também
pelo CONTEÚDO (qualquer sequência com cara de CPF/CNPJ/CNS em texto livre é mascarada). JSON é mascarado
recursivamente. Nada aqui registra valores em log.
"""
import re
from typing import Any

from app.identity import cns as cnsmod
from app.identity import documento as docmod

_DOC = re.compile(r"(cpf|cnpj|documento|^doc$|_doc$|^doc_|pis|^rg$|^rg_|_rg$)", re.I)
_CNS = re.compile(r"(cns|cartao_sus|cartaosus)", re.I)
_NOME = re.compile(r"(nome|^mae$|_mae$|^mae_|^pai$|_pai$|responsavel|falecido_nome)", re.I)
_END = re.compile(r"(endereco|logradouro|^end$|_end$|^end_|bairro|^cep$|_cep$|complemento|numero_casa|^rua$)", re.I)
_TEL = re.compile(r"(telefone|^fone|_fone$|celular|^tel$|_tel$|^tel_|whats)", re.I)
_EMAIL = re.compile(r"(e_?mail)", re.I)
_OBITO = re.compile(r"(obito|falecido)", re.I)
_NASC = re.compile(r"(nascimento|nasc$|^dt_nasc|^data_nasc|_nasc_|nascto)", re.I)
# campos de linhas "chave/valor" (silver.identificadores.valor, gold.cidadao_dados.valor) dependem do rótulo
_ROTULO_SENSIVEL = re.compile(r"(cpf|cnpj|cns|documento|nome|mae|endereco|logradouro|bairro|cep|telefone|celular|email|nascimento|obito|falecido)", re.I)

_CPF_TXT = re.compile(r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b")
_CNPJ_TXT = re.compile(r"\b\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}\b")
_EMAIL_TXT = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
_CNS_TXT = re.compile(r"\b\d{15}\b")

OCULTO = "••• (oculto)"


def tipo_sensivel(coluna: str) -> str | None:
    c = coluna or ""
    if _CNS.search(c):
        return "cns"
    if _DOC.search(c):
        return "documento"
    if _NOME.search(c):
        return "nome"
    if _END.search(c):
        return "endereco"
    if _OBITO.search(c):
        return "obito"
    if _NASC.search(c):
        return "nascimento"
    if _EMAIL.search(c):
        return "email"
    if _TEL.search(c):
        return "telefone"
    return None


def rotulo_sensivel(rotulo: str | None) -> bool:
    return bool(rotulo and _ROTULO_SENSIVEL.search(rotulo))


def _nome(v: str) -> str:
    """UX-E1: "MAR*** S***" (3 letras do primeiro nome, inicial dos demais)."""
    partes = [p for p in v.split() if p]
    if not partes:
        return OCULTO
    return " ".join([f"{partes[0][:3]}***", *(f"{p[0]}***" for p in partes[1:])])


def _tel(v: str) -> str:
    d = re.sub(r"\D", "", v)
    if len(d) >= 10:
        return f"({d[-11:-9] if len(d) >= 11 else d[:2]}) ****-**{d[-2:]}"
    return f"****-**{d[-2:]}" if len(d) >= 4 else OCULTO


def _texto_livre(v: str) -> str:
    v = _CNPJ_TXT.sub(lambda m: docmod.mascara(m.group(0)) or OCULTO, v)
    v = _CPF_TXT.sub(lambda m: docmod.mascara(m.group(0)) or OCULTO, v)
    v = _EMAIL_TXT.sub(lambda m: mascara_valor("email", m.group(0)), v)
    return _CNS_TXT.sub(lambda m: cnsmod.mascara(m.group(0)) or OCULTO, v)


def mascara_valor(tipo: str | None, v: Any) -> Any:
    if v is None or v == "":
        return v
    if isinstance(v, dict):
        return mascara_dict(v)
    if isinstance(v, list):
        return [mascara_valor(tipo, x) for x in v]
    if tipo is None:
        return _texto_livre(v) if isinstance(v, str) else v
    s = str(v)
    if tipo == "documento":
        return docmod.mascara(s) or OCULTO
    if tipo == "cns":
        return cnsmod.mascara(s) or OCULTO
    if tipo == "nome":
        return _nome(s)
    if tipo == "obito":
        return "••• (oculto)"
    if tipo == "nascimento":
        return "**/**/****"
    if tipo == "email":
        u, _, d = s.partition("@")
        return f"{u[:1]}***@{d}" if d else OCULTO
    if tipo == "telefone":
        return _tel(s)
    return OCULTO


def mascara_campo(coluna: str, v: Any) -> Any:
    return mascara_valor(tipo_sensivel(coluna), v)


def mascara_dict(d: dict) -> dict:
    return {k: mascara_campo(k, v) for k, v in d.items()}


def mascara_linha(linha: dict, rotulo_col: str | None = None, valor_col: str | None = None) -> dict:
    """Mascara uma linha; se a tabela é chave/valor, o valor herda a sensibilidade do rótulo (ex.: campo='cpf')."""
    out = mascara_dict(linha)
    if rotulo_col and valor_col and valor_col in linha and rotulo_sensivel(str(linha.get(rotulo_col) or "")):
        rot = str(linha.get(rotulo_col))
        out[valor_col] = mascara_valor(tipo_sensivel(rot) or "documento", linha[valor_col])
    return out


def mascara_restrita(coluna: str, v: Any, livres=frozenset()) -> Any:
    """Bronze (P1): coluna fora da allowlist do conector nunca sai em claro. Nome pessoal -> máscara do tipo;
    qualquer outro nome (NU_INSCR, Ident...) -> oculto. Coluna livre -> máscara normal (nome + conteúdo)."""
    if coluna in livres:
        return mascara_campo(coluna, v)
    if v is None or v == "":
        return v
    t = tipo_sensivel(coluna)
    return mascara_valor(t, v) if t and not isinstance(v, (dict, list)) else OCULTO
