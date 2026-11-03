"""Prata do RH (RH1): linha do CSV do portal -> silver.rh_remuneracao, com a secretaria padronizada.

Os nomes das secretarias mudam ao longo dos anos ("SECRETARIA DE EDUCAÇÃO", "SECRETARIA MUNICIPAL DE EDUCACAO"): sem
padronizar, a evolução por secretaria quebra na troca do nome. O texto original fica em secretaria_origem. Aposentados,
pensionistas, cedidos e indenizados aparecem no portal como "secretaria": viram o grupo inativos/outros.
"""
import unicodedata

from sqlalchemy import delete, insert
from sqlalchemy.orm import Session

from app.models.rh import Remuneracao
from app.silver.financas import data_br, valor

# (trecho no nome original, sem acento e em maiúsculas -> nome padronizado). A ordem importa: o primeiro que casar vale.
SECRETARIAS = (
    ("APOSENTAD", "Aposentados"), ("PENSIONIST", "Pensionistas"), ("CEDID", "Cedidos"), ("INDENIZAD", "Indenizados"),
    ("EDUCACAO", "Educação"), ("SAUDE", "Saúde"), ("ACAO SOCIAL", "Ação Social"), ("ASSISTENCIA SOCIAL", "Ação Social"),
    ("RECURSOS HUMANOS", "Administração e RH"), ("ADMINISTRACAO", "Administração e RH"), ("FINANCAS", "Finanças"),
    ("FAZENDA", "Finanças"), ("SEGURANCA", "Segurança"), ("MEIO AMBIENTE", "Meio Ambiente e Serviços"),
    ("SERVICOS", "Meio Ambiente e Serviços"), ("INFRAESTRUTURA", "Infraestrutura e Urbanismo"),
    ("URBANISMO", "Infraestrutura e Urbanismo"), ("OBRAS", "Infraestrutura e Urbanismo"),
    ("INOVACAO", "Inovação e Desenvolvimento"), ("DESENVOLVIMENTO", "Inovação e Desenvolvimento"),
    ("PLANEJAMENTO URBANO", "Infraestrutura e Urbanismo"), ("ESPORTE", "Esporte e Cultura"), ("CULTURA", "Esporte e Cultura"),
    ("ASSUNTOS ESTRATEGICOS", "Assuntos Estratégicos"), ("SOLIDARIEDADE", "Fundo de Solidariedade"),
    ("GABINETE", "Gabinete do Prefeito"), ("PROCURADORIA", "Procuradoria"),
)
GRUPOS = {"Aposentados": "inativos", "Pensionistas": "inativos", "Cedidos": "outros", "Indenizados": "outros"}
SEM_SECRETARIA = "Não informada"


def _sem_acento(v: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", v) if unicodedata.category(c) != "Mn").upper()


def secretaria(original: str | None) -> str:
    s = _sem_acento((original or "").strip())
    if not s:
        return SEM_SECRETARIA
    for trecho, nome in SECRETARIAS:
        if trecho in s:
            return nome
    return " ".join((original or "").split()).title()[:80]  # desconhecida: mantém legível, sem inventar


def _t(v: str | None, n: int) -> str | None:
    return (v or "").strip()[:n] or None


def projeta(r: dict, ano: int, mes: int) -> dict:
    sec = secretaria(r.get("Secretaria"))
    return {"ano": ano, "mes": mes, "matricula": r["Matrícula"][:20], "nome": _t(r.get("Nome"), 200), "situacao": _t(r.get("Situação"), 30),
            "cargo": _t(r.get("Cargo"), 120), "funcao_inativa": _t(r.get("Função inativa"), 120), "secretaria": sec,
            "secretaria_origem": _t(r.get("Secretaria"), 120), "grupo": GRUPOS.get(sec, "ativa"), "jornada": _t(r.get("Jornada"), 10),
            "referencia": _t(r.get("Referência"), 20), "salario_base": valor(r.get("Salário base")),
            "vencimentos": valor(r.get("Total vencimentos")), "descontos": valor(r.get("Total descontos")),
            "liquido": valor(r.get("Total líquido")), "admissao": data_br(r.get("Data de admissão")),
            "afastamento": data_br(r.get("Data de afastamento")), "demissao": data_br(r.get("Data de demissão"))}


def troca_mes(db: Session, ano: int, mes: int, linhas: list[dict], lote: int = 2000) -> int:
    """Troca as linhas do mês pelas do arquivo. Não confirma (o chamador confirma)."""
    db.execute(delete(Remuneracao).where(Remuneracao.ano == ano, Remuneracao.mes == mes))
    proj = [projeta(r, ano, mes) for r in linhas]
    for i in range(0, len(proj), lote):
        db.execute(insert(Remuneracao), proj[i:i + lote])
    return len(proj)
