"""Conector da API SIL (tributário novo), SOMENTE LEITURA, como leitor da carga genérica (amostra, carga completa, agenda).

Mesma interface de leitor que o SqlServerConnector (abrir, ler_pagina, ler_amostra, verificar_chave, assinatura), para a
SIL entrar no mesmo carregador, monitor e agenda e cair em silver.empresas_sil.

Unificação (TL 28/10): este leitor NÃO fala HTTP. Toda chamada passa pelo ClienteSil (cliente.py): allowlist
(checa_operacao) antes de qualquer rede, homologação (sandbox) primeiro, produção só em erro de rede, expires_in
validado, token com trava, retry/backoff/throttle e logs sem segredo. Não existe outro caminho HTTP no pacote.

Empresas: GET /franca/empresa/consultarlote?Sequencia=N devolve as próximas 50 empresas com CCM >= N. Paginação por
keyset (último CCM + 1); acaba em lista vazia/404. Teto de lotes por execução (SIL_MAX_LOTES_POR_EXECUCAO, padrão 1)
até a primeira amostra ser validada. E-mail e telefone da empresa não entram.
"""
import logging
import time
from collections.abc import Callable
from typing import Any

import httpx

from app.connectors.sil.cliente import ClienteSil, ErroSil, SilDesabilitado
from app.connectors.sil.config import ConfigSil

__all__ = ["ConfigSil", "ErroSil", "SilConnector", "achata_empresa"]

log = logging.getLogger("sigpmf.sil")

LOTE_API = 50
CAMINHO_LOTE = "/franca/empresa/consultarlote"
TABELA_EMPRESAS = "Empresas"
COLUNAS_EMPRESAS = ("Ccm", "CNPJCPF", "Nome", "PossuiDebito", "Endereco", "Bairro", "CEP", "Complemento",
                    "AreaOcupada", "AreaUtilizada")
MSG_NAO_CONFIGURADA = "API SIL não configurada no ambiente (SIL_BASE_URL, SIL_CLIENT_ID, SIL_CLIENT_SECRET)."


class _Sessao:
    """Uma 'conexão' da carga: o ClienteSil e quantos lotes já foram lidos nesta execução."""

    def __init__(self, cliente: ClienteSil):
        self.cliente, self.lotes, self.truncado = cliente, 0, False
        self.ultimo_lote_cheio = False  # o último lote veio com LOTE_API itens: a API provavelmente tem mais
        self.alerta: dict | None = None  # página com CCM repetido / sem CCM novo: a execução para (sem pular checkpoint)

    @property
    def ha_mais(self) -> bool:
        """Parou no teto de lotes com a API ainda devolvendo lotes cheios: a execução é PARCIAL."""
        return self.truncado and self.ultimo_lote_cheio

    def parcial(self) -> dict | None:
        if self.alerta:
            return {"parcial": True, "lotes_lidos": self.lotes, "ha_mais": True, "alerta": self.alerta}
        return {"parcial": True, "lotes_lidos": self.lotes, "ha_mais": True} if self.ha_mais else None

    def close(self) -> None:
        self.cliente.fechar()


def _txt(v: Any) -> str | None:
    """A API devolve {} ou "" para campo vazio."""
    if v is None or isinstance(v, (dict, list)):
        return None
    s = str(v).strip()
    return s or None


def achata_empresa(e: dict) -> dict:
    local = e.get("LocalEmpresa") or {}
    return {"Ccm": int(str(e["Ccm"]).strip()), "CNPJCPF": _txt(e.get("CNPJCPF")), "Nome": _txt(e.get("Nome")),
            "PossuiDebito": _txt(e.get("PossuiDebito")), "Endereco": _txt(local.get("Endereco")),
            "Bairro": _txt(local.get("Bairro")), "CEP": _txt(local.get("CEP")),
            "Complemento": _txt(local.get("Complemento")), "AreaOcupada": _txt(e.get("AreaOcupada")),
            "AreaUtilizada": _txt(e.get("AreaUtilizada"))}


def _empresas(j: Any) -> list | None:
    """Lista de empresas da resposta: {"empresas": [...]} (visto em produção) ou {"data": {"empresas": [...]}} (doc)."""
    if isinstance(j, dict):
        if isinstance(j.get("empresas"), list):
            return j["empresas"]
        d = j.get("data")
        if isinstance(d, dict) and isinstance(d.get("empresas"), list):
            return d["empresas"]
    return None


class SilConnector:
    def __init__(self, config: ConfigSil | None = None, nome: str = "sil", transporte: httpx.BaseTransport | None = None,
                 esperar: Callable[[float], None] = time.sleep, relogio: Callable[[], float] = time.monotonic,
                 amostra_limite: int | None = None, max_lotes: int | None = None):
        self.config = config or ConfigSil.do_ambiente()
        self.nome, self._transporte, self._esperar, self._relogio = nome, transporte, esperar, relogio
        if amostra_limite is None:
            from app.core.config import get_settings
            amostra_limite = int(get_settings().amostra_limite)
        self.amostra_limite = amostra_limite
        self.max_lotes = max(1, int(max_lotes or self.config.max_lotes_por_execucao or 1))
        self.ultima_parcial: dict | None = None

    # ------------------------------------------------------------------ HTTP (só pelo ClienteSil)
    def abrir(self) -> _Sessao:
        if not self.config.habilitado:
            raise SilDesabilitado(MSG_NAO_CONFIGURADA)
        return _Sessao(ClienteSil(self.config, transport=self._transporte, relogio=self._relogio, dormir=self._esperar))

    # ------------------------------------------------------------------ empresas
    def lote_empresas(self, s: _Sessao, sequencia: int) -> list[dict]:
        j = s.cliente.get(CAMINHO_LOTE, {"Sequencia": int(sequencia)})  # allowlist + token + retry no ClienteSil
        s.lotes += 1
        if j is None:  # 404: fim da sequência
            return []
        itens = _empresas(j)
        s.ultimo_lote_cheio = isinstance(itens, list) and len(itens) >= LOTE_API
        if itens is None:
            if isinstance(j, dict) and not j:
                return []
            raise ErroSil("Resposta inesperada da API SIL em empresa/consultarlote.")
        linhas = [achata_empresa(x.get("empresa") or {}) for x in itens
                  if isinstance(x, dict) and (x.get("empresa") or {}).get("Ccm")]
        validas = [r for r in linhas if r["Ccm"] >= sequencia]
        unicas: dict[int, dict] = {}
        for r in validas:  # 1 linha por CCM (a 1ª): o checkpoint só anda por CCM distinto de fato gravado
            unicas.setdefault(r["Ccm"], r)
        repetidas, fora = len(validas) - len(unicas), len(linhas) - len(validas)
        if itens and (repetidas or not unicas):
            # Página com CCM repetido ou sem nenhum CCM novo (>= Sequencia): a execução para aqui, parcial com alerta,
            # em vez de gastar o teto de lotes em duplicatas. Só contagens no log/auditoria (nada de PII).
            s.alerta = {"motivo": "ccm_repetido" if repetidas else "sem_ccm_novo", "sequencia": int(sequencia),
                        "itens": len(itens), "ccms_distintos": len(unicas), "repetidas": repetidas,
                        "abaixo_da_sequencia": fora}
            log.warning("SIL: página anômala em consultarlote %s; execução interrompida", s.alerta)
        return sorted(unicas.values(), key=lambda r: r["Ccm"])

    # ------------------------------------------------------------------ interface de leitor
    def _checa_tabela(self, tabela: str) -> None:
        if tabela.split(".")[-1] != TABELA_EMPRESAS:
            raise ErroSil(f"Tabela {tabela} não existe no conector SIL.")

    def ler_pagina(self, conn: _Sessao, tabela: str, politica, ordem, ultima: tuple | None, limite: int,
                   desde=None) -> list[dict]:
        """Keyset por CCM: acumula lotes de 50 até `limite`, até a API acabar ou até o teto de lotes da execução
        (`max_lotes`, por sessão). No teto devolve o que leu e as próximas chamadas devolvem [] (fim da execução)."""
        self._checa_tabela(tabela)
        seq = int(ultima[0]) + 1 if ultima and ultima[0] is not None else 1
        out: list[dict] = []
        while len(out) < limite:
            if conn.alerta:
                break
            if conn.lotes >= self.max_lotes:
                if not conn.truncado:
                    conn.truncado = True
                    log.info("SIL: teto de %d lote(s) por execução atingido (SIL_MAX_LOTES_POR_EXECUCAO)",
                             self.max_lotes)
                break
            lote = self.lote_empresas(conn, seq)
            out.extend(lote)
            if conn.alerta:  # para sem pedir outra página; o checkpoint fica no último CCM gravado
                conn.truncado = True
                break
            if not lote:
                break
            if conn.lotes >= self.max_lotes and conn.ultimo_lote_cheio:
                conn.truncado = True  # teto atingido e a API ainda tem lotes: execução parcial
            prox = lote[-1]["Ccm"] + 1
            if prox <= seq:  # nunca repete a mesma Sequencia
                break
            seq = prox
        return out

    def ler_amostra(self, tabela: str, politica=None, campo_incremental=None, desde=None) -> list[dict]:
        conn = self.abrir()
        try:
            out = self.ler_pagina(conn, tabela, politica, ("Ccm",), None, self.amostra_limite)[: self.amostra_limite]
            if conn.lotes >= self.max_lotes and conn.ultimo_lote_cheio:
                conn.truncado = True
            self.ultima_parcial = conn.parcial()  # a amostra genérica registra "parcial" na execução e na auditoria
            return out
        finally:
            conn.close()

    def verificar_chave(self, conn, tabela: str, politica, ordem) -> dict:
        """CCM é a chave da própria API (única, nunca nula). Sem contagem na API: total desconhecido."""
        self._checa_tabela(tabela)
        return {"total": None, "nulos": 0, "distintas": None}

    def ler_nulos(self, conn, tabela, politica, ordem, limite) -> list:
        return []

    def assinatura(self, conn, tabela: str, politica, campo_incremental) -> list:
        return [None, "indisponivel"]  # sem contagem/data na API: a agenda nunca pula a SIL

    def testar_conexao(self) -> dict:
        """Só a autenticação (sondagem sandbox-first do ClienteSil); nenhum GET de dados."""
        try:
            s = self.abrir()
            try:
                s.cliente.sondar()
            finally:
                s.close()
            return {"ok": True, "erro": None}
        except ErroSil as exc:
            return {"ok": False, "erro": str(exc)}

    def diagnostico(self) -> dict:
        r = self.testar_conexao()
        erro = None if r["ok"] else {"classe": "ErroSil", "numero": None, "motivo": r["erro"]}
        return {"ok": r["ok"], "banco": "API SIL", "erro": erro}
