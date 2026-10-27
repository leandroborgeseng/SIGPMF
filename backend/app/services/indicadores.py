"""Indicador por base: contagens por camada lidas das tabelas reais.

Mapeamento base → camadas (via controle.bases_catalogo.sistemas, códigos de sistema):
- bronze    = COUNT(*) bronze.registros            WHERE sistema_origem IN sistemas
- prata     = COUNT(*) silver.vinculos             WHERE sistema IN sistemas  (registro correlacionado a um cidadão)
- ouro      = COUNT(DISTINCT cidadao_id) gold.cidadao_dados WHERE sistema IN sistemas (cidadãos prontos para indicador)
- pendentes = COUNT(*) silver.correlacoes_pendentes WHERE sistema IN sistemas AND status = 'pendente'
- ultima_carga = controle.execucoes mais recente com sistema = conjunto da base (ex.: SAUDE) ou conjunto combinado
  que a contenha (ex.: SAUDE+CEMITERIO).
Base sem sistemas (sem conector) → zeros e ultima_carga nula.
"""
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.connectors.sqlserver.politicas import politica
from app.models.camadas import CorrelacaoPendente, DadoGold, RegistroBronze, Vinculo
from app.models.controle import BaseCatalogo, Execucao


def _por_sistema(db: Session, stmt) -> dict[str, int]:
    return {k: int(v) for k, v in db.execute(stmt).all()}


TIPOS_AMOSTRA = ("carga_demo_amostra", "amostra_conector", "carga_municipe_amostra")


def bloqueio(b: BaseCatalogo) -> str | None:
    """Motivo para não carregar (None = pode). Sem lista de colunas aprovada nunca carrega (nada de SELECT *)."""
    if b.status == "excluido":
        return "Base descartada na triagem"
    if b.status != "implementado" or not b.conjunto:
        return "Conector ainda não implementado" if b.status == "triado" else "Sem lista de colunas aprovada"
    if not b.sistemas or any(politica(s) is None for s in b.sistemas):
        return "Sem lista de colunas aprovada"
    return None


def ultima_execucao(execs: list[Execucao], b: BaseCatalogo, tipos: tuple[str, ...] | None = None) -> Execucao | None:
    conj = (b.conjunto or "").upper()
    if not conj:
        return None
    return next((e for e in execs if conj in (e.sistema or "").split("+") and (tipos is None or e.tipo in tipos)), None)


def indicadores(db: Session) -> list[dict]:
    bronze = _por_sistema(db, select(RegistroBronze.sistema_origem, func.count()).group_by(RegistroBronze.sistema_origem))
    prata = _por_sistema(db, select(Vinculo.sistema, func.count()).group_by(Vinculo.sistema))
    ouro = _por_sistema(db, select(DadoGold.sistema, func.count(func.distinct(DadoGold.cidadao_id))).group_by(DadoGold.sistema))
    # ISS (Munícipe) tem silver própria (silver.municipes), fora de silver.vinculos: conta lá
    from app.models.camadas import Municipe
    prata["ISS"] = prata.get("ISS", 0) + int(db.scalar(select(func.count()).select_from(Municipe)) or 0)
    pend = _por_sistema(db, select(CorrelacaoPendente.sistema, func.count())
                        .where(CorrelacaoPendente.status == "pendente").group_by(CorrelacaoPendente.sistema))
    execs = list(db.scalars(select(Execucao).where(Execucao.sistema.is_not(None)).order_by(Execucao.id.desc()).limit(500)))
    out = []
    bases = db.scalars(select(BaseCatalogo).order_by(BaseCatalogo.prioridade.is_(None), BaseCatalogo.prioridade, BaseCatalogo.nome))
    for b in bases:
        sis = list(b.sistemas or [])
        ult = ultima_execucao(execs, b)
        amostra = ultima_execucao(execs, b, TIPOS_AMOSTRA)
        motivo = bloqueio(b)
        out.append({
            "id": b.id, "nome": b.nome, "descricao": b.descricao, "grupo": b.grupo, "triagem": b.triagem,
            "status": b.status, "prioridade": b.prioridade, "conector_id": b.conjunto, "tem_conector": b.status == "implementado",
            "pode_carregar": motivo is None, "motivo_bloqueio": motivo,
            "tem_amostra": amostra is not None and amostra.status in ("sucesso", "sem_novidades"),
            **_completa(db, b),
            "bloqueio": _bloqueio(db, b),
            "com_erro": ult is not None and ult.status == "falha",
            "origem_vazia": ult is not None and ult.status == "origem_vazia",
            "contagens": {"bronze": sum(bronze.get(s, 0) for s in sis), "prata": sum(prata.get(s, 0) for s in sis),
                          "ouro": sum(ouro.get(s, 0) for s in sis), "pendentes": sum(pend.get(s, 0) for s in sis)},
            "ultima_carga": None if ult is None else {
                "id": ult.id, "tipo": ult.tipo, "status": ult.status, "iniciado_em": ult.iniciado_em,
                "finalizado_em": ult.finalizado_em, "lidos": ult.lidos, "novos": ult.novos,
                "atualizados": ult.atualizados, "ignorados": ult.ignorados, "mensagem": ult.mensagem,
                "erro": ult.erro_publico()},
        })
    return out


def _bloqueio(db: Session, b: BaseCatalogo) -> dict | None:
    """CC12: execução em andamento que segura o conector (aviso âmbar na tela). Sem PII."""
    from app.services import carga_conector as cc
    from app.services import trava as tv
    if (b.conjunto or "") not in cc.CONECTORES or b.status != "implementado":
        return None
    sistema = cc.CONECTORES[b.conjunto][0]
    if not tv.em_andamento(db, sistema):
        return None
    return tv.estado(db, sistema, tv.NOMES.get(b.conjunto, b.conjunto))


def _completa(db: Session, b: BaseCatalogo) -> dict:
    """CC1: liberação da carga completa real por conector (Saúde/Cemitério) e o motivo quando bloqueada."""
    from app.services import carga_completa as ccomp
    if (b.conjunto or "") not in ccomp.CONECTORES_COMPLETA or b.status != "implementado":
        return {"completa_disponivel": False, "completa_liberada": False, "completa_motivo": None}
    motivo = ccomp.motivo_bloqueio(db, b.conjunto)
    rodando = ccomp.ativa(db, ccomp._sistema(b.conjunto)) is not None  # CC7.4: card esconde números parciais
    return {"completa_disponivel": True, "completa_liberada": motivo is None, "completa_motivo": motivo,
            "completa_em_andamento": rodando}


def totais(bases: list[dict]) -> dict[str, int]:
    return {k: sum(b["contagens"][k] for b in bases) for k in ("bronze", "prata", "ouro", "pendentes")}
