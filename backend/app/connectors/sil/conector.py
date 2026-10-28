"""Conector da API SIL (tributário novo), SOMENTE LEITURA (só GET de consulta; nunca os POST de atualização).

Mesma interface de leitor que o SqlServerConnector usa na amostra, na carga completa e na agenda (abrir, ler_pagina,
ler_amostra, verificar_chave, assinatura), para a SIL entrar no mesmo carregador, monitor e agenda.

Empresas: GET /franca/empresa/consultarlote?Sequencia=N devolve as próximas 50 empresas com CCM >= N (conferido em
produção em 28/10/2026). Paginação por keyset: a próxima chamada começa no último CCM + 1; acaba quando vem vazio.
A API não tem contagem nem data de alteração: a carga é sempre completa e a reconciliação fica "sem contagem".

Token OAuth2 (client_credentials, expira em ~10 min) renovado sozinho; 401 renova e repete uma vez; 429/5xx/rede
repetem com espera. Nenhuma mensagem leva token, secret ou client_id. E-mail e telefone da empresa não entram.
"""
import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx

from app.core.config import Settings, get_settings

log = logging.getLogger("sigpmf.sil")

LOTE_API = 50
TABELA_EMPRESAS = "Empresas"
COLUNAS_EMPRESAS = ("Ccm", "CNPJCPF", "Nome", "PossuiDebito", "Endereco", "Bairro", "CEP", "Complemento",
                    "AreaOcupada", "AreaUtilizada")


class ErroSil(RuntimeError):
    """Falha da API SIL com mensagem segura (sem token, secret nem client_id)."""


@dataclass(repr=False)
class ConfigSil:
    base_url: str | None
    client_id: str | None
    client_secret: str | None
    scope: str | None
    grant_type: str = "client_credentials"
    timeout: int = 60
    intervalo_ms: int = 150
    amostra_limite: int = 1000

    @classmethod
    def do_ambiente(cls, s: Settings | None = None) -> "ConfigSil":
        s = s or get_settings()
        return cls(s.sil_base_url, s.sil_client_id, s.sil_client_secret, s.sil_scope, s.sil_grant_type,
                   int(s.sil_timeout), int(s.sil_intervalo_ms), int(s.amostra_limite))

    def configurado(self) -> bool:
        return bool(self.base_url and self.client_id and self.client_secret)

    def __repr__(self) -> str:  # nunca expõe credenciais (logs, tracebacks)
        return f"ConfigSil(base_url={self.base_url!r}, client_id=***, client_secret=***, scope=***, timeout={self.timeout})"

    __str__ = __repr__


class _Sessao:
    """Uma 'conexão' da carga: cliente HTTP + token vigente."""

    def __init__(self, cliente: httpx.Client):
        self.cliente, self.token, self.expira = cliente, None, 0.0

    def close(self) -> None:
        self.cliente.close()


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


def _segundos(v: Any) -> int:
    try:
        return int(str(v).rstrip("s"))
    except (TypeError, ValueError):
        return 600


class SilConnector:
    def __init__(self, config: ConfigSil | None = None, nome: str = "sil", transporte: httpx.BaseTransport | None = None,
                 esperar=time.sleep):
        self.config = config or ConfigSil.do_ambiente()
        self.nome, self._transporte, self._esperar = nome, transporte, esperar

    # ------------------------------------------------------------------ HTTP
    def abrir(self) -> _Sessao:
        if not self.config.configurado():
            raise ErroSil("API SIL não configurada no ambiente (SIL_BASE_URL, SIL_CLIENT_ID, SIL_CLIENT_SECRET).")
        base = self.config.base_url.rstrip("/") + "/"
        return _Sessao(httpx.Client(base_url=base, timeout=self.config.timeout, transport=self._transporte))

    def _autentica(self, s: _Sessao) -> None:
        c = self.config
        try:
            r = s.cliente.post("api/authenticate", data={
                "client_id": c.client_id, "client_secret": c.client_secret, "grant_type": c.grant_type,
                "scope": c.scope or ""})
        except httpx.HTTPError as exc:
            raise ErroSil(f"API SIL não respondeu na autenticação ({type(exc).__name__}).") from None
        if r.status_code != 200:
            raise ErroSil(f"API SIL recusou a autenticação (HTTP {r.status_code}). Conferir SIL_CLIENT_ID/SECRET/SCOPE.")
        j = r.json()
        if "access_token" not in j:
            raise ErroSil("API SIL não devolveu token na autenticação.")
        s.token, s.expira = j["access_token"], time.monotonic() + _segundos(j.get("expires_in"))

    def _get(self, s: _Sessao, caminho: str, params: dict) -> tuple[int, Any]:
        renovou = False
        for tentativa in range(4):
            if s.token is None or time.monotonic() > s.expira - 60:
                self._autentica(s)
            try:
                r = s.cliente.get(caminho, params=params, headers={"Authorization": f"Bearer {s.token}"})
            except httpx.HTTPError as exc:
                if tentativa == 3:
                    raise ErroSil(f"API SIL não respondeu ({type(exc).__name__}) em {caminho}.") from None
                self._esperar(2 ** tentativa)
                continue
            if r.status_code == 401 and not renovou:
                s.token, renovou = None, True
                continue
            if r.status_code == 429 or r.status_code >= 500:
                if tentativa == 3:
                    raise ErroSil(f"API SIL indisponível (HTTP {r.status_code}) em {caminho}.")
                self._esperar(2 ** tentativa)
                continue
            if self.config.intervalo_ms:
                self._esperar(self.config.intervalo_ms / 1000)  # gentileza com a API do fornecedor
            try:
                return r.status_code, r.json()
            except ValueError:
                return r.status_code, None
        raise ErroSil(f"API SIL não respondeu em {caminho}.")

    # ------------------------------------------------------------------ empresas
    def lote_empresas(self, s: _Sessao, sequencia: int) -> list[dict]:
        st, j = self._get(s, "franca/empresa/consultarlote", {"Sequencia": int(sequencia)})
        if isinstance(j, dict) and isinstance(j.get("empresas"), list):
            linhas = [achata_empresa(x.get("empresa") or {}) for x in j["empresas"] if (x.get("empresa") or {}).get("Ccm")]
            return sorted((r for r in linhas if r["Ccm"] >= sequencia), key=lambda r: r["Ccm"])
        if st in (200, 204, 404):  # fim da sequência (sem lista)
            return []
        raise ErroSil(f"Resposta inesperada da API SIL em empresa/consultarlote (HTTP {st}).")

    # ------------------------------------------------------------------ interface de leitor
    def _checa_tabela(self, tabela: str) -> None:
        if tabela.split(".")[-1] != TABELA_EMPRESAS:
            raise ErroSil(f"Tabela {tabela} não existe no conector SIL.")

    def ler_pagina(self, conn: _Sessao, tabela: str, politica, ordem, ultima: tuple | None, limite: int,
                   desde=None) -> list[dict]:
        """Keyset por CCM: acumula lotes de 50 até `limite` ou até a API acabar (lote vazio)."""
        self._checa_tabela(tabela)
        seq = int(ultima[0]) + 1 if ultima and ultima[0] is not None else 1
        out: list[dict] = []
        while len(out) < limite:
            lote = self.lote_empresas(conn, seq)
            if not lote:
                break
            out.extend(lote)
            seq = lote[-1]["Ccm"] + 1
        return out

    def ler_amostra(self, tabela: str, politica=None, campo_incremental=None, desde=None) -> list[dict]:
        conn = self.abrir()
        try:
            return self.ler_pagina(conn, tabela, politica, ("Ccm",), None, self.config.amostra_limite)[
                : self.config.amostra_limite]
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
        try:
            s = self.abrir()
            try:
                self._autentica(s)
            finally:
                s.close()
            return {"ok": True, "erro": None}
        except ErroSil as exc:
            return {"ok": False, "erro": str(exc)}

    def diagnostico(self) -> dict:
        r = self.testar_conexao()
        erro = None if r["ok"] else {"classe": "ErroSil", "numero": None, "motivo": r["erro"]}
        return {"ok": r["ok"], "banco": "API SIL", "erro": erro}
