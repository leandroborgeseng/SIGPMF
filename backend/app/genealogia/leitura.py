"""Lê só a vizinhança de 2 níveis em gold.genealogia_arestas.

Não percorre a cidade. A consulta inteira fica com statement_timeout de 10s.
Tabela ausente, geração ainda não gravada ou tempo esgotado viram ArvoreIndisponivel.
"""
import re
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import text
from sqlalchemy.orm import Session

TIMEOUT = "10s"
_TEMPO = re.compile(r"^\d+(ms|s)$")

MENSAGENS = {
    "nao_materializada": (
        "A árvore genealógica ainda não foi calculada. "
        "É preciso popular gold.genealogia_arestas antes desta leitura."
    ),
    "timeout": "A consulta da árvore genealógica passou do tempo limite e foi interrompida.",
}


class ArvoreIndisponivel(Exception):
    def __init__(self, motivo: str, mensagem: str):
        self.motivo = motivo
        self.mensagem = mensagem
        super().__init__(mensagem)


@dataclass
class PessoaLinha:
    cidadao_id: str
    nome: str | None
    cpf: str | None
    nascimento: date | None


@dataclass
class ArestaLinha:
    origem: str
    destino: str
    sentido: str
    base: str
    forca: str
    parentesco: str
    confianca: float
    criterio: str | None
    fonte_atualizado_em: datetime | None


@dataclass
class Vizinhanca:
    geracao: int
    arestas: list[ArestaLinha]
    pessoas: list[PessoaLinha]


def _texto(exc: BaseException) -> str:
    orig = getattr(exc, "orig", None)
    return f"{type(exc).__name__} {exc} {type(orig).__name__ if orig else ''} {orig or ''}".lower()


def eh_timeout(exc: BaseException) -> bool:
    t = _texto(exc)
    return "timeout" in t or "querycanceled" in t or "canceling statement" in t


def _ausente(exc: BaseException) -> bool:
    t = _texto(exc)
    return "does not exist" in t or "undefinedtable" in t or "genealogia_" in t


def executa(db: Session, sql: str, params: dict | None = None, timeout: str | None = None):
    """Roda a consulta. Tempo esgotado ou tabela ausente vira ArvoreIndisponivel e desfaz a transação."""
    try:
        if timeout is not None:
            if not _TEMPO.fullmatch(timeout):
                raise ValueError(timeout)
            db.execute(text(f"SET LOCAL statement_timeout = '{timeout}'"))
        return db.execute(text(sql), params or {})
    except ArvoreIndisponivel:
        raise
    except Exception as exc:
        db.rollback()
        if eh_timeout(exc):
            raise ArvoreIndisponivel("timeout", MENSAGENS["timeout"]) from exc
        if _ausente(exc):
            raise ArvoreIndisponivel("nao_materializada", MENSAGENS["nao_materializada"]) from exc
        raise


def _linhas(rows) -> list[ArestaLinha]:
    out = []
    for r in rows:
        out.append(ArestaLinha(
            origem=r.origem, destino=r.destino, sentido=r.sentido or "outro", base=r.base, forca=r.forca,
            parentesco=r.parentesco, confianca=round(float(r.confianca), 2), criterio=r.criterio,
            fonte_atualizado_em=r.fonte_atualizado_em))
    return out


def _arestas(db: Session, ids: list[str], geracao: int) -> list[ArestaLinha]:
    if not ids:
        return []
    rows = executa(db, """
        SELECT origem, destino, sentido, base, forca, parentesco, confianca, criterio, fonte_atualizado_em
          FROM gold.genealogia_arestas
         WHERE geracao = :g AND (origem = ANY(:ids) OR destino = ANY(:ids))
    """, {"g": geracao, "ids": ids}).all()
    return _linhas(rows)


def _pontas(arestas: list[ArestaLinha]) -> set[str]:
    ids: set[str] = set()
    for a in arestas:
        ids.add(a.origem)
        ids.add(a.destino)
    return ids


def _pessoas(db: Session, ids: list[str]) -> list[PessoaLinha]:
    if not ids:
        return []
    rows = executa(db, """
        SELECT DISTINCT ON (c.cidadao_id) c.cidadao_id, c.nome, c.data_nascimento, i.valor AS cpf
          FROM silver.cidadaos c
          LEFT JOIN silver.identificadores i ON i.cidadao_id = c.cidadao_id AND i.tipo = 'cpf'
         WHERE c.cidadao_id = ANY(:ids)
         ORDER BY c.cidadao_id, i.id
    """, {"ids": ids}).all()
    return [PessoaLinha(r.cidadao_id, r.nome, r.cpf, r.data_nascimento) for r in rows]


def carregar(db: Session, cidadao_id: str) -> Vizinhanca:
    """Arestas que tocam o foco e, em seguida, as que tocam esses vizinhos (até o 2º nível)."""
    row = executa(
        db, "SELECT geracao, populado_em FROM gold.genealogia_estado WHERE id = 1", timeout=TIMEOUT,
    ).first()
    if row is None or row.populado_em is None:
        raise ArvoreIndisponivel("nao_materializada", MENSAGENS["nao_materializada"])
    geracao = int(row.geracao)
    primeiras = _arestas(db, [cidadao_id], geracao)
    vizinhos = _pontas(primeiras) - {cidadao_id}
    segundas = _arestas(db, sorted(vizinhos), geracao) if vizinhos else []
    vistas: dict[tuple, ArestaLinha] = {}
    for a in primeiras + segundas:
        vistas[(a.origem, a.destino, a.base, a.sentido, a.forca, a.parentesco)] = a
    arestas = list(vistas.values())
    pessoas = _pessoas(db, sorted(_pontas(arestas) | {cidadao_id}))
    return Vizinhanca(geracao=geracao, arestas=arestas, pessoas=pessoas)
