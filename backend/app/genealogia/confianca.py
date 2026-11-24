"""Confiança do vínculo fraco (nome de mãe ou de pai em texto livre).

1,00 fica reservado ao vínculo forte (CPF na origem). O fraco nunca passa de 0,99.
O piso para emitir a aresta é 0,50. Estes nominais não passam por k-anonimato.
"""
from dataclasses import dataclass
from datetime import date
from difflib import SequenceMatcher

from app.identity.correlacao import normaliza_nome

# Texto devolvido pela API e mostrado na ficha. Os testes e a tela usam este critério, não outro.
CRITERIO_FRACO = (
    "Vínculo fraco (provável): o nome da mãe ou do pai está em texto livre e casa com uma pessoa já conhecida. "
    "Confiança 0,70 se o nome normalizado (sem acento, maiúsculas, espaços únicos) é idêntico; "
    "0,55 se a similaridade de sequência (difflib) é de pelo menos 0,90; "
    "0,50 se o índice de Jaccard dos tokens é de pelo menos 0,75. "
    "Soma 0,15 se o nascimento do filho é posterior ao do candidato e a diferença de idade fica entre 14 e 60 anos. "
    "Soma 0,15 se o CEP de 8 dígitos ou o logradouro normalizado coincide. "
    "Teto 0,99. Piso para emitir a aresta: 0,50. "
    "Se vários candidatos têm o nome idêntico, só entra quem tem o bônus de idade ou de endereço; "
    "sem um único candidato com bônus, o caso é ambíguo e não gera aresta. "
    "A busca por similaridade olha só quem compartilha o primeiro token, no máximo 200 candidatos. "
    "Se já existe aresta forte de geração entre o par, a fraca não é emitida. "
    "Na materialização em SQL, o nome idêntico, o Jaccard, a idade, o CEP, o piso e o teto são os mesmos. "
    "A faixa de similaridade alta usa pg_trgm similarity de pelo menos 0,90 quando a extensão existe "
    "(não é o difflib); sem a extensão, essa faixa não é emitida. "
    "O bloqueio por primeiro token olha no máximo 200 candidatos, na ordem do cidadao_id."
)

PISO = 0.50
TETO = 0.99
EXATO = 0.70
SEQUENCIA = 0.55
TOKENS = 0.50
BONUS = 0.15
LIMITE_SIMILARIDADE = 200


@dataclass(frozen=True)
class Nota:
    score: float
    criterio: str
    exato: bool
    bonus: bool


def _cep(v) -> str | None:
    d = "".join(ch for ch in str(v or "") if ch.isdigit())
    return d if len(d) == 8 else None


def _jaccard(a: str, b: str) -> float:
    ta, tb = set(a.split()), set(b.split())
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def idade_compativel(nasc_filho: date | None, nasc_candidato: date | None) -> bool:
    if not nasc_filho or not nasc_candidato or nasc_filho <= nasc_candidato:
        return False
    anos = (nasc_filho - nasc_candidato).days / 365.25
    return 14 <= anos <= 60


def endereco_coincide(cep_a, log_a, cep_b, log_b) -> bool:
    ca, cb = _cep(cep_a), _cep(cep_b)
    if ca and ca == cb:
        return True
    la, lb = normaliza_nome(log_a), normaliza_nome(log_b)
    return bool(la and la == lb)


def pontua(nome_texto: str, nome_candidato: str, *, nasc_filho: date | None, nasc_candidato: date | None,
           cep_filho, log_filho, cep_candidato, log_candidato) -> Nota | None:
    """Nota de um par. None quando o nome não chega no piso."""
    n1, n2 = normaliza_nome(nome_texto), normaliza_nome(nome_candidato)
    if not n1 or not n2:
        return None
    if n1 == n2:
        base, exato, rotulo = EXATO, True, "nome normalizado idêntico"
    else:
        if n1.split()[0] != n2.split()[0]:
            return None
        exato = False
        if SequenceMatcher(None, n1, n2).ratio() >= 0.90:
            base, rotulo = SEQUENCIA, "similaridade de sequência ≥ 0,90"
        elif _jaccard(n1, n2) >= 0.75:
            base, rotulo = TOKENS, "Jaccard de tokens ≥ 0,75"
        else:
            return None
    extras = []
    if idade_compativel(nasc_filho, nasc_candidato):
        extras.append("diferença de idade compatível")
    if endereco_coincide(cep_filho, log_filho, cep_candidato, log_candidato):
        extras.append("CEP ou logradouro coincidente")
    if extras:
        rotulo = rotulo + " + " + " + ".join(extras)
    return Nota(score=min(TETO, round(base + BONUS * len(extras), 2)), criterio=rotulo, exato=exato, bonus=bool(extras))
