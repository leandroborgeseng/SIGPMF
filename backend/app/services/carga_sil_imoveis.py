"""Carga do CSV do cadastro físico de imóveis da SIL para bronze.sil_imoveis_fisico.

A escrita é no Postgres do SIGPMF. O pacote do conector da API continua só leitura.

A API não traz esta tabela. O arquivo real é latin1, separado por `;`, com cabeçalho e um `;`
sobrando no fim de cada linha (a coluna vazia final é ignorada). Leitura e upsert em lotes:
INSERT ... ON CONFLICT (idfisico) DO UPDATE. Rodar de novo não duplica; linha igual não é regravada.

Mensagem, log e controle só levam contagens. CPF/CNPJ fica na coluna e não sai no texto da execução.
"""
from __future__ import annotations

import csv
import logging
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import TextIO

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.controle import Execucao, Log

log = logging.getLogger("sigpmf.sil")

SISTEMA = "SIL"
TIPO = "carga_sil_imoveis_fisico"
TABELA = "sil_imoveis_fisico"
TAMANHO_LOTE = 2000
_ID_MAX = 2**63 - 1

COLUNAS = (
    "idfisico",
    "inscrfisico",
    "situacaofisico",
    "crcresponsavel",
    "nomeresponsavel",
    "cpfcnpjresponsavel",
    "imovelcep",
    "imoveltplogradouro",
    "imovellogradouro",
    "imovelendereco",
    "imovelbairro",
    "entregacep",
    "entregalogradouro",
    "entregaendereco",
    "entregabairro",
)
_TEXTO = COLUNAS[1:]

_INSERT = """
INSERT INTO bronze.sil_imoveis_fisico (
    idfisico, inscrfisico, situacaofisico, crcresponsavel, nomeresponsavel, cpfcnpjresponsavel,
    imovelcep, imoveltplogradouro, imovellogradouro, imovelendereco, imovelbairro,
    entregacep, entregalogradouro, entregaendereco, entregabairro, execucao_id, atualizado_em
) VALUES (
    :idfisico, :inscrfisico, :situacaofisico, :crcresponsavel, :nomeresponsavel, :cpfcnpjresponsavel,
    :imovelcep, :imoveltplogradouro, :imovellogradouro, :imovelendereco, :imovelbairro,
    :entregacep, :entregalogradouro, :entregaendereco, :entregabairro, :execucao_id, now()
)
ON CONFLICT (idfisico) DO UPDATE SET
    inscrfisico = EXCLUDED.inscrfisico,
    situacaofisico = EXCLUDED.situacaofisico,
    crcresponsavel = EXCLUDED.crcresponsavel,
    nomeresponsavel = EXCLUDED.nomeresponsavel,
    cpfcnpjresponsavel = EXCLUDED.cpfcnpjresponsavel,
    imovelcep = EXCLUDED.imovelcep,
    imoveltplogradouro = EXCLUDED.imoveltplogradouro,
    imovellogradouro = EXCLUDED.imovellogradouro,
    imovelendereco = EXCLUDED.imovelendereco,
    imovelbairro = EXCLUDED.imovelbairro,
    entregacep = EXCLUDED.entregacep,
    entregalogradouro = EXCLUDED.entregalogradouro,
    entregaendereco = EXCLUDED.entregaendereco,
    entregabairro = EXCLUDED.entregabairro,
    execucao_id = EXCLUDED.execucao_id,
    atualizado_em = now()
"""

_SELECT = (
    "SELECT idfisico, inscrfisico, situacaofisico, crcresponsavel, nomeresponsavel, cpfcnpjresponsavel, "
    "imovelcep, imoveltplogradouro, imovellogradouro, imovelendereco, imovelbairro, "
    "entregacep, entregalogradouro, entregaendereco, entregabairro "
    "FROM bronze.sil_imoveis_fisico WHERE idfisico = ANY(:ids)"
)


class ArquivoInvalido(ValueError):
    """CSV fora do formato. A mensagem não leva valor de coluna."""


def _vazia(row: list[str]) -> bool:
    return not row or all(not (c or "").strip() for c in row)


def _normaliza(row: list[str], linha: int) -> list[str]:
    if len(row) == len(COLUNAS) + 1 and not row[-1].strip():
        row = row[:-1]
    if len(row) != len(COLUNAS):
        raise ArquivoInvalido(f"linha {linha}: quantidade de colunas")
    return [c.strip() for c in row]


def _registro(campos: list[str], linha: int) -> dict:
    bruto = campos[0]
    if not bruto.isdigit():
        raise ArquivoInvalido(f"linha {linha}: idfisico inválido")
    ident = int(bruto)
    if ident <= 0 or ident > _ID_MAX:
        raise ArquivoInvalido(f"linha {linha}: idfisico inválido")
    reg = {"idfisico": ident}
    for nome, valor in zip(_TEXTO, campos[1:], strict=True):
        reg[nome] = valor or None
    return reg


def iter_lotes(stream: TextIO, tamanho: int = TAMANHO_LOTE) -> Iterator[tuple[int, int, list[dict]]]:
    """Gera (linhas lidas, duplicadas no lote, registros únicos). O último idfisico do lote prevalece."""
    if tamanho < 1:
        raise ValueError("lote menor que 1")
    leitor = csv.reader(stream, delimiter=";")
    header = next(leitor, None)
    if header is None:
        raise ArquivoInvalido("arquivo vazio")
    nomes = _normaliza(header, 1)
    if nomes != list(COLUNAS):
        raise ArquivoInvalido("cabeçalho diferente do cadastro físico da SIL")
    lote: dict[int, dict] = {}
    lidos = dups = 0
    for numero, row in enumerate(leitor, start=2):
        if _vazia(row):
            continue
        reg = _registro(_normaliza(row, numero), numero)
        if reg["idfisico"] in lote:
            dups += 1
        lote[reg["idfisico"]] = reg
        lidos += 1
        if len(lote) >= tamanho:
            yield lidos, dups, list(lote.values())
            lote, lidos, dups = {}, 0, 0
    if lote:
        yield lidos, dups, list(lote.values())


def _igual(atual, novo: dict) -> bool:
    return all(atual[c] == novo[c] for c in _TEXTO)


def _aplicar(db: Session, regs: list[dict], execucao_id: int) -> tuple[int, int, int]:
    ids = [r["idfisico"] for r in regs]
    atuais = {r["idfisico"]: r for r in db.execute(text(_SELECT), {"ids": ids}).mappings()}
    gravar = []
    novos = atualizados = ignorados = 0
    for reg in regs:
        ant = atuais.get(reg["idfisico"])
        if ant is None:
            novos += 1
            gravar.append(reg)
        elif _igual(ant, reg):
            ignorados += 1
        else:
            atualizados += 1
            gravar.append(reg)
    if gravar:
        db.execute(text(_INSERT), [{**r, "execucao_id": execucao_id} for r in gravar])
    return novos, atualizados, ignorados


def _mensagem(ex: Execucao) -> str:
    return (f"tabela={TABELA} lidos={ex.lidos} novos={ex.novos} atualizados={ex.atualizados} "
            f"ignorados={ex.ignorados} duplicados={ex.duplicados} lotes={ex.lotes}")


def _abre(db: Session, usuario: str | None) -> Execucao:
    ex = Execucao(tipo=TIPO, sistema=SISTEMA, status="em_andamento",
                  usuario=usuario[:200] if usuario else None,
                  modo="completa", etapa="leitura", tabela_atual=TABELA, atualizado_em=datetime.now(UTC),
                  lidos=0, novos=0, atualizados=0, ignorados=0, duplicados=0, lotes=0)
    db.add(ex)
    db.commit()
    return ex


def _falha(db: Session, ex_id: int, exc: Exception) -> None:
    db.rollback()
    ex = db.get(Execucao, ex_id)
    ex.status = "falha"
    ex.etapa = "concluida"
    ex.finalizado_em = ex.atualizado_em = datetime.now(UTC)
    if isinstance(exc, ArquivoInvalido):
        ex.erro_classe = "arquivo_invalido"
        ex.mensagem = f"Arquivo inválido: {exc}"
    else:
        ex.registra_erro(exc)
        ex.mensagem = f"Falha na carga do cadastro físico da SIL ({type(exc).__name__})."
    db.add(Log(execucao_id=ex.id, nivel="ERROR", mensagem=ex.mensagem))
    db.commit()


def _carregar(db: Session, stream: TextIO, usuario: str | None, lote: int) -> Execucao:
    lotes = iter_lotes(stream, lote)
    primeiro = next(lotes, None)  # cabeçalho inválido não abre execução
    ex = _abre(db, usuario)
    try:
        fila = primeiro
        while fila is not None:
            lidos, dups, regs = fila
            novos, atualizados, ignorados = _aplicar(db, regs, ex.id)
            ex.lidos += lidos
            ex.duplicados += dups
            ex.novos += novos
            ex.atualizados += atualizados
            ex.ignorados += ignorados
            ex.lotes += 1
            ex.atualizado_em = datetime.now(UTC)
            db.commit()
            fila = next(lotes, None)
        ex.status = "sucesso" if ex.lidos else "sem_novidades"
        ex.etapa = "concluida"
        ex.finalizado_em = ex.atualizado_em = datetime.now(UTC)
        ex.mensagem = _mensagem(ex)
        db.add(Log(execucao_id=ex.id, nivel="INFO", mensagem=ex.mensagem))
        db.commit()
        log.info("sil imoveis fisico %s", ex.mensagem)
    except Exception as exc:  # noqa: BLE001 — registra a falha e devolve o mesmo erro ao comando
        _falha(db, ex.id, exc)
        raise
    return ex


def carregar(db: Session, origem: str | Path | TextIO, *, usuario: str | None = None,
             lote: int = TAMANHO_LOTE) -> Execucao:
    """Lê o CSV (caminho ou stream já em latin1) e grava a bronze. Confirma cada lote."""
    if hasattr(origem, "read"):
        return _carregar(db, origem, usuario, lote)
    with open(origem, encoding="latin-1", newline="") as stream:
        return _carregar(db, stream, usuario, lote)
