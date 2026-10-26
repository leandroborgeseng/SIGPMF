"""Ingestão bronze: JSONB bruto + origem + hash + execução. Idempotente por (origem, id, hash).
Cada lote é confirmado (commit) separadamente: se a carga falhar, a próxima retoma sem duplicar."""
import hashlib
import json
from collections.abc import Callable
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models.camadas import RegistroBronze
from app.models.controle import Log


def hash_registro(dados: dict[str, Any]) -> str:
    canon = json.dumps(dados, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)
    return hashlib.sha256(canon.encode()).hexdigest()


def ingere(db: Session, sistema: str, tabela: str, chave: str, campo_data: str | None,
           registros: list[dict[str, Any]], execucao_id: int, lote: int = 500,
           antes_do_lote: Callable[[int], None] | None = None) -> dict[str, int]:
    c = {"lidos": len(registros), "novos": 0, "atualizados": 0, "ignorados": 0, "lotes": 0}
    for n, i in enumerate(range(0, len(registros), lote), start=1):
        if antes_do_lote:
            antes_do_lote(n)
        linhas: dict[tuple[str, str], dict] = {}
        for r in registros[i:i + lote]:
            d = r.get(campo_data) if campo_data else None
            h = hash_registro(r)
            linhas[(str(r[chave]), h)] = {
                "sistema_origem": sistema, "tabela": tabela, "id_origem": str(r[chave]), "dados": r, "hash": h,
                "execucao_id": execucao_id, "atualizado_origem_em": datetime.fromisoformat(d) if d else None}
        ids = [k[0] for k in linhas]
        existentes = set(db.scalars(select(RegistroBronze.id_origem).where(
            RegistroBronze.sistema_origem == sistema, RegistroBronze.tabela == tabela,
            RegistroBronze.id_origem.in_(ids))))
        stmt = insert(RegistroBronze).values(list(linhas.values())).on_conflict_do_nothing(
            index_elements=["sistema_origem", "tabela", "id_origem", "hash"]).returning(RegistroBronze.id_origem)
        inseridos = [row[0] for row in db.execute(stmt)]
        atual = sum(1 for x in inseridos if x in existentes)
        c["atualizados"] += atual
        c["novos"] += len(inseridos) - atual
        c["ignorados"] += len(registros[i:i + lote]) - len(inseridos)
        c["lotes"] += 1
        db.add(Log(execucao_id=execucao_id, nivel="INFO",
                   mensagem=f"{sistema}.{tabela} lote {n}: lidos={len(registros[i:i + lote])} gravados={len(inseridos)}"))
        db.commit()  # lote confirmado
    return c
