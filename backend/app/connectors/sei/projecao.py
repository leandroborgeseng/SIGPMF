"""Projeção do que vem do SEI para a bronze (SEI5, SEI6) e visão por perfil.

- Processo: só gravado se nivelAcessoLocal E nivelAcessoGlobal forem "0" (público). Restrito (1), sigiloso (2) ou
  nível ausente: não gravado (só contado). Observações, link de acesso e usuários não entram.
- Documento: só metadados (id, tipo, série, protocolo do processo, unidade, nível de acesso). Nome do arquivo, título,
  "informacao", conteúdo e anexos não entram. Documento com nível 1/2 não é gravado.
- Andamento: id, processo, tarefa, data/hora e unidade. Descrição (texto livre) e usuário não entram. O
  andamentosCompletoResponse (OpenAPI) não traz nível de acesso: o sigilo vem do processo pai já gravado como
  público na bronze (mesmo ambiente). Processo restrito, sigiloso ou desconhecido: andamento descartado.
- Interessado: CPF válido (campo cpf ou sigla com 11 dígitos) -> cidadao_id (uuid5 do CPF, peso 1.0). Sem CPF
  válido: o nome vai para revisão (pendencia=revisao_nome), sem vínculo automático.
- Visão: CPF e nome em claro só para admin_lgpd via revelar (motivo + auditoria); os demais veem mascarado.
"""
from typing import Any, NamedTuple

from app.identity.correlacao import cidadao_id_por_cpf
from app.identity.cpf import mascara, normaliza, valido
from app.identity.nome import mascara_nome

PUBLICO = "0"
PERFIS_REVELAM = frozenset({"admin_lgpd"})


def _t(v: Any) -> str | None:
    if v is None or isinstance(v, (dict, list)):
        return None
    s = str(v).strip()
    return s or None


def _unidade(u: Any) -> dict | None:
    if not isinstance(u, dict):
        return None
    return {"id_unidade": _t(u.get("idUnidade")), "sigla": _t(u.get("sigla"))}


def nivel(item: dict, *campos: str) -> str | None:
    """Nível de acesso mais restritivo entre os campos presentes; None se nenhum vier."""
    vals = [_t(item.get(c)) for c in campos if _t(item.get(c)) is not None]
    if not vals:
        return None
    return max(vals)


def processo_publico(det: dict) -> bool:
    return (_t(det.get("nivelAcessoLocal")) == PUBLICO and _t(det.get("nivelAcessoGlobal")) == PUBLICO)


def projeta_interessado(i: dict) -> dict:
    doc = normaliza(_t(i.get("cpf")) or _t(i.get("sigla")) or "") or ""
    nome = _t(i.get("nome"))
    if len(doc) == 11 and valido(doc):
        return {"cpf": doc, "nome": nome, "cidadao_id": cidadao_id_por_cpf(doc), "peso": 1.0, "pendencia": None}
    return {"cpf": None, "nome": nome, "cidadao_id": None, "peso": None,
            "pendencia": "revisao_nome" if nome else "sem_identificacao"}


def projeta_processo(det: dict, ambiente: str) -> dict:
    tipos = det.get("TipoProcedimento") or []
    tipo = tipos[0] if isinstance(tipos, list) and tipos and isinstance(tipos[0], dict) else {}
    ger = det.get("andamentoGeracao") if isinstance(det.get("andamentoGeracao"), dict) else {}
    return {"id_procedimento": _t(det.get("idProcedimento")), "protocolo": _t(det.get("procedimentoFormatado")),
            "data_autuacao": _t(det.get("dataAutuacao")), "nivel_acesso": PUBLICO,
            "tipo": {"id": _t(tipo.get("idTipoProcedimento")), "nome": _t(tipo.get("nome"))},
            "unidade_geradora": _unidade(ger.get("unidade")),
            "assuntos": [_t(a.get("codigoEstruturado")) for a in det.get("assuntos") or [] if isinstance(a, dict)],
            "interessados": [projeta_interessado(i) for i in det.get("interessados") or [] if isinstance(i, dict)],
            "ambiente_origem": ambiente}


def projeta_documento(d: dict, protocolo: str | None, ambiente: str) -> dict | None:
    nv = nivel(d, "nivelAcesso", "nivelAcessoLocal", "nivelAcessoGlobal")
    if nv != PUBLICO:  # sem nível = sigiloso: não grava
        return None
    return {"id_documento": _t(d.get("idDocumento")), "tipo": _t(d.get("tipo")), "id_serie": _t(d.get("idSerie")),
            "id_procedimento": _t(d.get("idProcedimento")),
            "protocolo_processo": _t(d.get("procedimentoFormatado")) or protocolo,
            "unidade": _unidade(d.get("Unidade")), "nivel_acesso": nv, "ambiente_origem": ambiente}


def digitos(v: Any) -> str | None:
    """B6: protocolo comparado só pelos dígitos ("99990.000001/2026-01" == "99990000001202601")."""
    t = _t(v)
    d = "".join(ch for ch in t if ch.isdigit()) if t else ""
    return d or None


class Publicos(NamedTuple):
    """Processos públicos já na bronze: idProcedimento -> protocolo em dígitos, e dígitos -> idProcedimento."""
    por_id: dict[str, str | None]
    por_protocolo: dict[str, str]

    @property
    def ids(self) -> frozenset[str]:
        return frozenset(self.por_id)

    @property
    def protocolos(self) -> frozenset[str]:
        return frozenset(self.por_protocolo)


class NaoPublicos(NamedTuple):
    """Marcadores mínimos (0023) de processos restritos/sigilosos: por id e por protocolo em dígitos."""
    ids: frozenset[str]
    protocolos: frozenset[str]


PUBLICO_OK, RESTRITO, DESCONHECIDO, INCONSISTENTE = "publico", "restrito", "desconhecido", "inconsistente"


def classifica_andamento(a: dict, publicos: Publicos, nao_publicos: NaoPublicos | None = None) -> str:
    """B5: só é público se id E protocolo (dígitos) apontam para o MESMO processo público da bronze.
    Restrito conhecido (id ou protocolo no marcador) tem precedência; id e protocolo de processos distintos =
    inconsistente; sem nenhum dos dois conhecido = desconhecido (consulta o pai)."""
    idp, prot = _t(a.get("idProcesso")), digitos(a.get("protocoloProcedimento"))
    np = nao_publicos or NaoPublicos(frozenset(), frozenset())
    if (idp and idp in np.ids) or (prot and prot in np.protocolos):
        return RESTRITO
    if idp and prot and idp in publicos.por_id and publicos.por_id[idp] == prot:
        return PUBLICO_OK
    if (idp and idp in publicos.por_id) or (prot and prot in publicos.por_protocolo):
        return INCONSISTENTE
    return DESCONHECIDO


def processo_do_andamento_publico(a: dict, publicos: Publicos) -> bool:
    return classifica_andamento(a, publicos) == PUBLICO_OK


def projeta_andamento(a: dict, ambiente: str, publicos: Publicos, nao_publicos: NaoPublicos | None = None
                      ) -> dict | None:
    """Só grava andamento cujo processo pai está na bronze como público; o resto não é gravado (revela existência).
    Campos de nível no próprio andamento são ignorados (não existem no OpenAPI)."""
    if classifica_andamento(a, publicos, nao_publicos) != PUBLICO_OK:
        return None
    return {"id_andamento": _t(a.get("idAndamento")), "id_processo": _t(a.get("idProcesso")),
            "protocolo": _t(a.get("protocoloProcedimento")), "id_tarefa": _t(a.get("idTarefa")),
            "data_hora": _t(a.get("dataHora")), "unidade": _unidade(a.get("unidade")), "ambiente_origem": ambiente}


def nivel_processo(det: dict) -> str | None:
    """Nível mais restritivo entre local e global; None (sem nível) é tratado como sigiloso por quem chama."""
    return nivel(det, "nivelAcessoLocal", "nivelAcessoGlobal")


def visao_interessados(lista: list[dict], perfil: str, revela: bool = False) -> list[dict]:
    """Fora do revelar (admin_lgpd + motivo), CPF e nome saem mascarados para todos os perfis."""
    if revela and perfil in PERFIS_REVELAM:
        return [dict(i) for i in lista]
    return [{**i, "cpf": mascara(i.get("cpf")) if i.get("cpf") else None, "nome": mascara_nome(i.get("nome"))}
            for i in lista]
