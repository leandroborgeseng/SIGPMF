"""Regras do modelo prata (PR-03, S3/S4).

- S3/D3: cada valor candidato guarda a procedência (sistema, tabela, chave em hash, data). Vence o valor mais completo;
  empate → o mais recente; empate de novo → ordem estável (sistema, tabela). Nenhum candidato é apagado: só a marca
  `vencedor` muda.
- S4/N3: a ligação empresa–cidadão só acontece com CPF válido (DV, não repetido, fora da lista de preenchimento).
- Vínculo: determinístico tem peso 1.0 e status `confirmado`; probabilístico nunca grava `confirmado`.
"""
import hashlib
import hmac
import unicodedata
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.identity import cpf as cpf_mod
from app.models.prata_modelo import CidadaoEmpresa, ProcedenciaValor

METODOS_DETERMINISTICOS = frozenset({"cpf", "cnpj", "inscricao", "cns", "chave_origem", "obito_confirmado",
                                     "confirmacao_humana"})


class ChaveHashSemSegredo(RuntimeError):
    """SIGPMF_CHAVE_HASH_SEGREDO ausente: a prata do modelo não calcula chave sem o segredo."""


def segredo_chave_hash() -> bytes:
    s = get_settings().segredo_chave_hash_ok()
    if s is None:  # ausente, curto ou só espaços/tabs
        raise ChaveHashSemSegredo("SIGPMF_CHAVE_HASH_SEGREDO ausente ou inválido (mínimo 32 caracteres úteis)")
    return s.encode("utf-8")


def chave_hash(valor: Any) -> str:
    """HMAC-SHA256 (segredo do ambiente) da chave normalizada: a chave crua (CPF, inscrição) nunca vai para a prata
    do modelo nem para log, e o hash não pode ser revertido por força bruta sem o segredo."""
    return hmac.new(segredo_chave_hash(), str(valor).strip().encode("utf-8"), hashlib.sha256).hexdigest()


def cpf_apto_para_ligacao(valor: Any) -> bool:
    """N3/N3a pela porta única do PR-01 (`cpf.classifica`): DV, tamanho, dígitos iguais e lista CPF_PREENCHIMENTO."""
    return cpf_mod.classifica(valor).valor is not None


def status_vinculo(metodo: str, confianca: float) -> tuple[str, float]:
    """(status, confianca) a gravar. Probabilístico nunca vira `confirmado`, nem com confiança 1.0."""
    if metodo in METODOS_DETERMINISTICOS:
        return "confirmado", 1.0
    return "revisao", min(float(confianca), 0.99)


def completude(valor: Any) -> int:
    if valor is None:
        return 0
    s = unicodedata.normalize("NFKC", str(valor)).strip()
    return len([c for c in s if not c.isspace()])


def registra_valor(db: Session, *, entidade: str, entidade_id: str, campo: str, valor: Any, sistema: str,
                   tabela: str, chave_origem: Any, data_referencia: datetime | None) -> ProcedenciaValor:
    """Upsert do candidato (por origem) e recálculo do vencedor do campo. Sem DELETE."""
    h = chave_hash(chave_origem)
    atual = db.scalar(select(ProcedenciaValor).where(
        ProcedenciaValor.entidade == entidade, ProcedenciaValor.entidade_id == entidade_id,
        ProcedenciaValor.campo == campo, ProcedenciaValor.sistema == sistema, ProcedenciaValor.tabela == tabela,
        ProcedenciaValor.chave_hash == h))
    texto = None if valor is None else str(valor).strip() or None
    if atual is None:
        atual = ProcedenciaValor(entidade=entidade, entidade_id=entidade_id, campo=campo, sistema=sistema,
                                 tabela=tabela, chave_hash=h)
        db.add(atual)
    atual.valor, atual.completude, atual.data_referencia = texto, completude(texto), data_referencia
    db.flush()
    _recalcula_vencedor(db, entidade, entidade_id, campo)
    return atual


def _recalcula_vencedor(db: Session, entidade: str, entidade_id: str, campo: str) -> None:
    cands = list(db.scalars(select(ProcedenciaValor).where(
        ProcedenciaValor.entidade == entidade, ProcedenciaValor.entidade_id == entidade_id,
        ProcedenciaValor.campo == campo)))
    def ordem(p: ProcedenciaValor):
        ts = p.data_referencia.timestamp() if p.data_referencia else 0.0
        return (-p.completude, p.data_referencia is None, -ts, p.sistema, p.tabela, p.id)

    venc = min((p for p in cands if p.completude > 0), key=ordem, default=None)
    for p in cands:
        p.vencedor = p is venc
    db.flush()


def valor_vencedor(db: Session, entidade: str, entidade_id: str, campo: str) -> ProcedenciaValor | None:
    return db.scalar(select(ProcedenciaValor).where(
        ProcedenciaValor.entidade == entidade, ProcedenciaValor.entidade_id == entidade_id,
        ProcedenciaValor.campo == campo, ProcedenciaValor.vencedor.is_(True)))


class CpfInvalidoParaLigacao(ValueError):
    codigo = "cpf_invalido"


def liga_cidadao_empresa(db: Session, *, cidadao_id: str, empresa_id: int, cpf: Any, papel: str, sistema: str,
                         tabela: str) -> CidadaoEmpresa:
    """S4: exige CPF válido (N3). A chave guardada é o hash do CPF normalizado."""
    if not cpf_apto_para_ligacao(cpf):
        raise CpfInvalidoParaLigacao("cpf_invalido")
    h = chave_hash(cpf_mod.normaliza(cpf))
    lig = db.scalar(select(CidadaoEmpresa).where(CidadaoEmpresa.cidadao_id == cidadao_id,
                                                 CidadaoEmpresa.empresa_id == empresa_id, CidadaoEmpresa.papel == papel))
    if lig is None:
        lig = CidadaoEmpresa(cidadao_id=cidadao_id, empresa_id=empresa_id, papel=papel, sistema=sistema, tabela=tabela,
                             chave_hash=h)
        db.add(lig)
        db.flush()
    return lig
