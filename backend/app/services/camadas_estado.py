"""IN1: prata e ouro só são reconstruídas quando alguma ENTRADA mudou desde a última reconstrução bem-sucedida.

A prata/ouro é determinística (A14): o mesmo resultado sai das mesmas entradas. As entradas são
- a bronze (total de linhas, maior id e linhas em quarentena: inserir ou pôr em quarentena muda a assinatura);
- as confirmações humanas de óbito (controle.confirmacoes_obito);
- os parâmetros da correlação (controle.parametros);
- o CÓDIGO das regras (hash dos módulos de correlação, mapeamentos e montagem do ouro): um deploy que muda a regra
  força a reconstrução.
Assinatura igual à gravada -> a reconstrução é pulada (o resultado seria idêntico). Qualquer dúvida -> reconstrói.
Só a carga completa/incremental grava a assinatura; as outras reconstruções (amostra, reprocessar, confirmar óbito)
não gravam, então a próxima comparação dá diferente e reconstrói (direção segura).
"""
import hashlib
from functools import lru_cache
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.camadas import RegistroBronze
from app.models.controle import CamadasEstado, ConfirmacaoObito, Parametro

APP = Path(__file__).resolve().parent.parent
REGRAS = ("silver/processamento.py", "gold/visao.py", "ingestion/mapeamentos.py", "identity/correlacao.py",
          "identity/cns.py", "identity/cpf.py", "connectors/sqlserver/politicas.py")


@lru_cache
def hash_regras() -> str:
    h = hashlib.sha256()
    for r in REGRAS:
        p = APP / r
        h.update(r.encode())
        h.update(p.read_bytes() if p.exists() else b"-")
    return h.hexdigest()


def assinatura(db: Session) -> dict:
    total, maior, quar = db.execute(select(func.count(), func.max(RegistroBronze.id),
                                           func.count(RegistroBronze.quarentena))).one()
    conf_total, conf_maior = db.execute(select(func.count(), func.max(ConfirmacaoObito.id))).one()
    params = sorted((p.chave, p.valor) for p in db.scalars(select(Parametro)))
    return {"bronze": [int(total or 0), int(maior or 0), int(quar or 0)],
            "confirmacoes": [int(conf_total or 0), int(conf_maior or 0)],
            "parametros": hashlib.sha256(repr(params).encode()).hexdigest(),
            "regras": hash_regras()}


def gravada(db: Session) -> dict | None:
    e = db.get(CamadasEstado, 1)
    return e.assinatura if e is not None else None


def inalterada(db: Session, assin: dict) -> bool:
    return gravada(db) == assin


def registra(db: Session, assin: dict, execucao_id: int | None) -> None:
    """Grava (sem commit) a assinatura das entradas usadas na reconstrução que acabou de dar certo."""
    e = db.get(CamadasEstado, 1)
    if e is None:
        db.add(CamadasEstado(id=1, assinatura=assin, execucao_id=execucao_id))
    else:
        e.assinatura, e.execucao_id, e.reconstruido_em = assin, execucao_id, func.now()
