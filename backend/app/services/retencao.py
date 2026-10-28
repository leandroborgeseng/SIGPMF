"""Retenção automática (onda 7, PL2–PL3). Uma vez por dia, depois das cargas da agenda (ou "Executar agora", admin).

Regras SEGURAS para o SIGPMF (a prata/ouro é reconstruída da bronze):
- bronze: apaga só VERSÕES ANTIGAS de um registro — linhas que já têm uma versão mais nova do mesmo
  (sistema, tabela, id_origem) e foram extraídas há mais de RETENCAO_BRONZE_VERSOES_DIAS. A versão mais recente de cada
  registro NUNCA é apagada, nem linha citada por vínculo ou pendência da prata. Quarentena fica (tem fila própria).
- controle.logs: linhas técnicas mais antigas que RETENCAO_LOGS_DIAS.
- controle.auditoria: append-only (trigger no banco); NÃO é apagada aqui (prazo legal decidido com o DPO).
Cada execução vira uma linha em controle.execucoes (tipo "retencao"), visível no monitor, e é auditada.
"""
from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.audit import audita
from app.core.config import get_settings
from app.models.controle import Execucao

TIPO = "retencao"

_BRONZE = text("""
DELETE FROM bronze.registros r
 WHERE r.quarentena IS NULL
   AND r.extraido_em < :corte
   AND EXISTS (SELECT 1 FROM bronze.registros n
                WHERE n.sistema_origem = r.sistema_origem AND n.tabela = r.tabela AND n.id_origem = r.id_origem
                  AND n.id > r.id AND n.quarentena IS NULL)
   AND NOT EXISTS (SELECT 1 FROM silver.vinculos v WHERE v.bronze_id = r.id)
   AND NOT EXISTS (SELECT 1 FROM silver.correlacoes_pendentes p WHERE p.bronze_id = r.id)
""")
_LOGS = text("DELETE FROM controle.logs WHERE criado_em < :corte")


def ja_rodou_hoje(db: Session, agora: datetime | None = None) -> bool:
    agora = agora or datetime.now(UTC)
    inicio = agora.replace(hour=0, minute=0, second=0, microsecond=0)
    return db.query(Execucao).filter(Execucao.tipo == TIPO, Execucao.status == "sucesso",
                                     Execucao.iniciado_em >= inicio).first() is not None


def executar(db: Session, usuario: str = "agenda", agora: datetime | None = None) -> Execucao:
    s = get_settings()
    agora = agora or datetime.now(UTC)
    ex = Execucao(tipo=TIPO, sistema=None, status="em_andamento", usuario=usuario, atualizado_em=agora)
    db.add(ex)
    db.commit()
    try:
        b = db.execute(_BRONZE, {"corte": agora - timedelta(days=s.retencao_bronze_versoes_dias)}).rowcount or 0
        lg = db.execute(_LOGS, {"corte": agora - timedelta(days=s.retencao_logs_dias)}).rowcount or 0
        ex.status, ex.finalizado_em = "sucesso", datetime.now(UTC)
        ex.mensagem = (f"Retenção: {b} versão(ões) antiga(s) da bronze e {lg} log(s) técnico(s) apagados "
                       f"(versões > {s.retencao_bronze_versoes_dias} dias, logs > {s.retencao_logs_dias} dias). "
                       "A versão mais recente de cada registro e a auditoria são mantidas.")
        ex.lidos = b + lg
        db.commit()
        audita(db, usuario, "retencao_executada", "retencao", {"bronze_versoes": b, "logs": lg, "execucao_id": ex.id})
    except Exception as exc:  # noqa: BLE001 - registra e segue (a agenda não pode morrer por causa da limpeza)
        db.rollback()
        ex = db.get(Execucao, ex.id)
        ex.status, ex.finalizado_em, ex.mensagem = "falha", datetime.now(UTC), f"Retenção falhou ({type(exc).__name__})."
        db.commit()
    return ex
