"""Regras de correlação de identidade (aceite v2/v3).

- CPF válido idêntico: score 1,0, associa automaticamente (cidadao_id = uuid5 do CPF).
- Mesmo CPF com data de nascimento divergente: ambíguo, fica pendente.
- Sem CPF válido: score por nome normalizado, nascimento e nome da mãe (pesos em controle.parametros).
  Score >= limiar_revisao (0,85): vai para revisão (não associa nesta entrega).
  Abaixo: cria um novo cidadao_id.
"""
import unicodedata
import uuid
from dataclasses import dataclass

NAMESPACE = uuid.UUID("6f1c2a52-3b7e-4c1e-9a0d-5f0a1c2b3d4e")


@dataclass(frozen=True)
class Parametros:
    limiar_revisao: float = 0.85
    peso_nome: float = 0.40
    peso_nascimento: float = 0.30
    peso_mae: float = 0.25


def cidadao_id_por_cpf(cpf: str) -> str:
    return str(uuid.uuid5(NAMESPACE, f"cpf:{cpf}"))


def cidadao_id_por_origem(sistema: str, tabela: str, id_origem: str) -> str:
    return str(uuid.uuid5(NAMESPACE, f"origem:{sistema}:{tabela}:{id_origem}"))


def normaliza_nome(nome: str | None) -> str:
    if not nome:
        return ""
    s = unicodedata.normalize("NFKD", nome).encode("ascii", "ignore").decode()
    return " ".join(s.upper().split())


def score(a: dict, b: dict, p: Parametros) -> tuple[float, list[str]]:
    """a e b: dicts canônicos (nome, data_nascimento, nome_mae). Nunca chega a 1,0 sem CPF."""
    pts, attrs = 0.0, []
    if normaliza_nome(a.get("nome")) and normaliza_nome(a.get("nome")) == normaliza_nome(b.get("nome")):
        pts += p.peso_nome
        attrs.append("nome")
    if a.get("data_nascimento") and a.get("data_nascimento") == b.get("data_nascimento"):
        pts += p.peso_nascimento
        attrs.append("data_nascimento")
    if normaliza_nome(a.get("nome_mae")) and normaliza_nome(a.get("nome_mae")) == normaliza_nome(b.get("nome_mae")):
        pts += p.peso_mae
        attrs.append("nome_mae")
    return min(round(pts, 4), 0.99), attrs
