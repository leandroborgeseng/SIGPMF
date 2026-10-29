"""Regras do modelo prata (PR-03, S3/S4).

- S3/D3: cada valor candidato guarda a procedência (sistema, tabela, chave em hash, data). Vence o valor mais completo;
  empate → o mais recente; empate de novo → ordem estável (sistema, tabela). Nenhum candidato é apagado: só a marca
  `vencedor` muda.
- S4/N3: a ligação empresa–cidadão só acontece com CPF válido (DV, não repetido, fora da lista de preenchimento).
- Vínculo: determinístico tem peso 1.0 e status `confirmado`; probabilístico nunca grava `confirmado`.
"""
import hashlib
import os
import unicodedata
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.identity import cpf as cpf_mod
from app.models.prata_modelo import CidadaoEmpresa, ProcedenciaValor

METODOS_DETERMINISTICOS = frozenset({"cpf", "cnpj", "inscricao", "cns", "chave_origem", "obito_confirmado",
                                     "confirmacao_humana"})
# N3a: a lista de preenchimento vem de configuração (env), nunca de constante de dado no código de regra.
_ENV_PREENCHIMENTO = "CPF_PREENCHIMENTO"


def chave_hash(valor: Any) -> str:
    """sha256 da chave normalizada: a chave crua (CPF, inscrição) nunca vai para a prata do modelo nem para log."""
    return hashlib.sha256(str(valor).strip().encode("utf-8")).hexdigest()


def cpfs_preenchimento() -> frozenset[str]:
    bruto = os.environ.get(_ENV_PREENCHIMENTO, "12345678909")
    return frozenset(d for d in (cpf_mod.normaliza(x.strip()) for x in bruto.split(",")) if d)


def cpf_apto_para_ligacao(valor: Any) -> bool:
    d = cpf_mod.normaliza(valor)
    return bool(d) and cpf_mod.valido(d) and d not in cpfs_preenchimento()


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
