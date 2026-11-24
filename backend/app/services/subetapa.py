"""Subetapa e sinal de vida das cargas (PR-B: CT16–CT22, UXC10–UXC13).

- Tudo é gravado numa conexão SEPARADA (commit próprio), visível enquanto a transação da prata/ouro está aberta.
- `Pulso` mantém o sinal de vida a cada INTERVALO_S segundos mesmo durante uma consulta longa e sem contagem
  parcial (CT16/CT17): a tela distingue "rodando sem resultado" de "travada".
- Textos de subetapa são FIXOS no código (só nome de etapa, de tabela e contagens). `texto_seguro` recusa qualquer
  coisa com cara de dado pessoal (CPF, CNS, e-mail) — CT12/CT19.
- Nunca toca a origem: só escreve/lê controle.execucoes e controle.execucao_subetapas.
"""
from __future__ import annotations

import logging
import re
import threading
from datetime import UTC, datetime

from sqlalchemy import select, update

from app.core.db import get_engine
from app.models.controle import Execucao, ExecucaoSubetapa
from app.silver.processamento import Medicao

log = logging.getLogger("sigpmf.subetapa")
INTERVALO_S = 30.0
ETAPAS = ("leitura", "bronze", "prata", "ouro")

# Subetapas da prata, na ordem em que processamento.reconstruir as fecha (Medicao.marca).
SEQ_PRATA = (
    ("limpa_prata", "Prata: limpando a prata anterior", False),
    ("le_bronze_canonico", "Prata: lendo registros da bronze", False),
    ("indice_cns", "Prata: montando o índice de CNS", False),
    ("passe_cpf", "Prata: buscando duplicados por CPF", True),
    ("passe_cns", "Prata: correlacionando por CNS", True),
    ("passe_atributos", "Prata: correlacionando por atributos", True),
    ("passe_esus", "Prata: revisando registros do e-SUS", True),
    ("passe_obitos", "Prata: aplicando óbitos", True),
    ("grava_cidadaos", "Prata: gravando cidadãos", False),
    ("grava_identificadores", "Prata: gravando identificadores", False),
    ("grava_pendentes_inconsistencias", "Prata: gravando pendências e inconsistências", False),
    ("grava_vinculos", "Prata: gravando vínculos", False),
    ("grava_enderecos", "Prata: gravando endereços", False),
)
TEXTO_OURO = "Ouro: consolidando cidadãos e vínculos"
TEXTO_LEITURA = "Leitura: lendo tabelas da origem e gravando na bronze"

# CPF (com . ou - em qualquer posição), CNS (15 dígitos, com ou sem separadores) e e-mail
_PROIBIDO = re.compile(r"\d{3}[.\-]?\d{3}[.\-]?\d{3}[.\-]?\d{2}|\d{3}[.\-]?\d{4}[.\-]?\d{4}[.\-]?\d{4}|@")


class TextoInseguro(ValueError):
    pass


def texto_seguro(texto: str) -> str:
    """CT19: só nome de etapa/tabela e contagens; recusa padrões de CPF, CNS ou e-mail. Corta em 200 caracteres."""
    t = " ".join((texto or "").split())[:200]
    if not t or _PROIBIDO.search(t.replace(" ", "")):
        raise TextoInseguro("texto de subetapa recusado (parece dado pessoal)")
    return t


def _agora() -> datetime:
    return datetime.now(UTC)


def marca(ex_id: int, etapa: str, texto: str, contador: int | None = None, total: int | None = None) -> None:
    """Abre (ou atualiza) a subetapa atual: fecha a anterior no log se o texto mudou. Também é sinal de vida."""
    t = texto_seguro(texto)
    agora = _agora()
    with get_engine().begin() as c:
        atual = c.execute(select(Execucao.subetapa).where(Execucao.id == ex_id)).scalar()
        vals = {"etapa": etapa, "subetapa": t, "subetapa_contador": contador, "subetapa_total": total,
                "ultimo_sinal_em": agora, "atualizado_em": agora}
        if atual != t:
            c.execute(update(ExecucaoSubetapa).where(ExecucaoSubetapa.execucao_id == ex_id,
                                                     ExecucaoSubetapa.finalizada_em.is_(None))
                      .values(finalizada_em=agora))
            c.execute(ExecucaoSubetapa.__table__.insert().values(
                execucao_id=ex_id, etapa=etapa, subetapa=t, contador=contador, total=total, iniciada_em=agora))
            vals["subetapa_iniciada_em"] = agora
        else:
            c.execute(update(ExecucaoSubetapa).where(ExecucaoSubetapa.execucao_id == ex_id,
                                                     ExecucaoSubetapa.finalizada_em.is_(None))
                      .values(contador=contador, total=total))
        c.execute(update(Execucao).where(Execucao.id == ex_id).values(**vals))


def sinal(ex_id: int, contador: int | None = None, total: int | None = None) -> None:
    """Só o sinal de vida (e, se vier, o contador da subetapa atual)."""
    agora = _agora()
    vals: dict = {"ultimo_sinal_em": agora, "atualizado_em": agora}
    if contador is not None:
        vals.update(subetapa_contador=contador, subetapa_total=total)
    with get_engine().begin() as c:
        c.execute(update(Execucao).where(Execucao.id == ex_id).values(**vals))


def finaliza(ex_id: int) -> None:
    """Fecha a subetapa aberta (fim da execução, com sucesso ou não)."""
    with get_engine().begin() as c:
        c.execute(update(ExecucaoSubetapa).where(ExecucaoSubetapa.execucao_id == ex_id,
                                                 ExecucaoSubetapa.finalizada_em.is_(None))
                  .values(finalizada_em=_agora()))


class Pulso:
    """Context manager: thread que grava o sinal de vida a cada `intervalo` s (consultas longas, CT16/CT17)."""

    def __init__(self, ex_id: int, intervalo: float | None = None):
        self.ex_id, self.intervalo = ex_id, intervalo if intervalo is not None else INTERVALO_S
        self._parar = threading.Event()
        self._t: threading.Thread | None = None

    def _roda(self) -> None:
        while not self._parar.wait(self.intervalo):
            try:
                sinal(self.ex_id)
            except Exception:  # noqa: BLE001 — o pulso nunca derruba a carga
                log.warning("sinal de vida falhou (execução %s)", self.ex_id)

    def __enter__(self) -> Pulso:
        self._t = threading.Thread(target=self._roda, name=f"pulso-{self.ex_id}", daemon=True)
        self._t.start()
        return self

    def __exit__(self, *exc) -> None:
        self._parar.set()
        if self._t:
            self._t.join(timeout=5)


class MedicaoComSinal(Medicao):
    """Medicao da prata que, a cada etapa fechada, abre a próxima subetapa em texto (CT16) e encaminha o progresso."""

    def __init__(self, ex_id: int):
        super().__init__()
        self.ex_id = ex_id
        self._i = 0
        self._abre(0)

    def _abre(self, i: int) -> None:
        if i < len(SEQ_PRATA):
            try:
                marca(self.ex_id, "prata", SEQ_PRATA[i][1], 0 if SEQ_PRATA[i][2] else None, None)
            except Exception:  # noqa: BLE001
                log.warning("não gravou a subetapa (execução %s)", self.ex_id)

    def marca(self, nome: str, entrada: int | None = None, saida: int | None = None) -> None:
        super().marca(nome, entrada, saida)
        nomes = [n for n, _, _ in SEQ_PRATA]
        if nome in nomes:
            self._i = nomes.index(nome) + 1
            self._abre(self._i)

    def progresso(self, n: int, total: int | None) -> None:
        """progresso(processados, total) do reconstruir: contador da etapa e da subetapa contável atual."""
        agora = _agora()
        vals: dict = {"etapa": "prata", "processadas_etapa": n, "total_etapa": total,
                      "ultimo_sinal_em": agora, "atualizado_em": agora}
        if self._i < len(SEQ_PRATA) and SEQ_PRATA[self._i][2]:
            vals.update(subetapa_contador=n, subetapa_total=total)
        with get_engine().begin() as c:
            c.execute(update(Execucao).where(Execucao.id == self.ex_id).values(**vals))


    def subcontador(self, n: int, total: int | None) -> None:
        """Só o contador da subetapa contável atual (o da etapa vem do sinal_de_vida do chamador)."""
        if self._i < len(SEQ_PRATA) and SEQ_PRATA[self._i][2]:
            sinal(self.ex_id, n, total)


def progresso_ouro(ex_id: int):
    """Callback do visao.reconstruir: contador da etapa ouro e da subetapa."""
    def _p(n: int, total: int | None) -> None:
        agora = _agora()
        with get_engine().begin() as c:
            c.execute(update(Execucao).where(Execucao.id == ex_id).values(
                etapa="ouro", processadas_etapa=n, total_etapa=total, subetapa_contador=n, subetapa_total=total,
                ultimo_sinal_em=agora, atualizado_em=agora))
    return _p
