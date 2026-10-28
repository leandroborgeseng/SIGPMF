"""Cliente HTTP da SIL (SIL13: allowlist checada antes de qualquer chamada de rede): token OAuth2 client_credentials em cache, GET com timeout/retry/backoff/throttle e allowlist.

Regras (aceite SIL1-SIL3, SIL5):
- Único POST: AUTH_PATH. GET só nos caminhos de GET_PERMITIDOS. Qualquer outro método ou caminho, e qualquer caminho
  com um segmento proibido (escrita da SIL), levanta OperacaoProibida ANTES de abrir conexão.
- Token só em memória; renovado SIL margem antes de expirar; um 401 renova uma vez e repete; o segundo 401 vira
  FalhaCredencial.
- 429/5xx/timeout/erro de conexão: até `tentativas` repetições com backoff exponencial (Retry-After respeitado, com
  teto). 404 = não localizado (None).
- Log: só método, caminho (sem query), status, tentativa e duração. Nunca URL completa, chave consultada, corpo,
  token, client_id ou segredo.
"""
import logging
import threading
import time
from collections.abc import Callable
from typing import Any

import httpx

from app.connectors.sil.config import ConfigSil

log = logging.getLogger("sigpmf.conector.sil")
# O httpx/httpcore logam a URL completa (com a query: CPF/CNPJ/inscrição) em INFO/DEBUG. Só avisos e erros passam.
for _nome in ("httpx", "httpcore"):
    logging.getLogger(_nome).setLevel(logging.WARNING)

AUTH_PATH = "/api/authenticate"
# SIL13 / decisão TL 27/10 (v1): só estas rotas. parcelas (SIL12) e consultaprecopublico ficam fora.
GET_PERMITIDOS = frozenset({"/franca/imoveis", "/franca/empresas", "/franca/atividade", "/franca/empresa/consultarlote"})
# Bloqueio explícito (qualquer método), além da allowlist: escrita, rotas que geram cobrança e débitos fora da v1.
ROTAS_BLOQUEADAS = frozenset({"/franca/imoveis/atualizar", "/franca/precospublicos", "/franca/consultaprecopublico",
                              "/franca/parcelas"})
SEGMENTOS_PROIBIDOS = frozenset({"atualizar"})  # escrita da SIL (POST .../imoveis/atualizar): nunca chamar
# Preços Públicos geram cobrança (TL 27/10): qualquer segmento que, normalizado (sem acento, caixa, hífen ou "_"),
# contenha um destes radicais é barrado, em qualquer método. Débitos: fora da allowlist até o escopo sair.
RADICAIS_PROIBIDOS = ("precopublico", "precospublicos", "preco", "precos")


def _sem_acento(s: str) -> str:
    import unicodedata
    return "".join(ch for ch in unicodedata.normalize("NFKD", s) if not unicodedata.combining(ch))


def _segmento_proibido(seg: str) -> bool:
    from urllib.parse import unquote
    n = "".join(ch for ch in _sem_acento(unquote(seg)).lower() if ch.isalnum())
    return n in SEGMENTOS_PROIBIDOS or any(r in n for r in RADICAIS_PROIBIDOS)


class ErroSil(RuntimeError):
    """Erro do conector com mensagem segura (sem segredo, token, URL completa nem chave consultada)."""


class RespostaAuthInvalida(ErroSil):
    """Sandbox autenticou (200) mas a resposta não é utilizável: não tenta a produção."""


def parse_expires_in(valor: Any) -> float:
    """expires_in da SIL: 600, "600" ou "600s" (formato da especificação). Ausente -> 600. Inválido -> ValueError."""
    if valor is None or valor == "":
        return 600.0
    if isinstance(valor, bool):
        raise ValueError("expires_in inválido")
    if isinstance(valor, (int, float)):
        n = float(valor)
    elif isinstance(valor, str):
        t = valor.strip()
        if t[-1:].lower() == "s":
            t = t[:-1].strip()
        n = float(t)
    else:
        raise ValueError("expires_in inválido")
    if not (n > 0) or n == float("inf"):
        raise ValueError("expires_in inválido")
    return n


class SilDesabilitado(ErroSil):
    pass


class OperacaoProibida(ErroSil):
    pass


class FalhaCredencial(ErroSil):
    pass


class FalhaSil(ErroSil):
    def __init__(self, motivo: str, status: int | None = None):
        super().__init__(motivo)
        self.motivo, self.status = motivo, status


def _normaliza(caminho: str) -> str:
    if not caminho.startswith("/") or "?" in caminho or "#" in caminho or "\\" in caminho:
        raise OperacaoProibida("caminho inválido para a SIL")
    partes = [p for p in caminho.split("/") if p]
    if any(p in (".", "..") for p in partes):
        raise OperacaoProibida("caminho inválido para a SIL")
    return "/" + "/".join(partes)


def checa_operacao(metodo: str, caminho: str) -> str:
    """Allowlist: POST só na autenticação; GET só nos caminhos permitidos; segmento proibido sempre barrado."""
    c = _normaliza(caminho)
    if c.lower() in ROTAS_BLOQUEADAS or any(_segmento_proibido(p) for p in c.split("/")):
        raise OperacaoProibida("operação proibida neste conector (escrita ou rota que gera cobrança na SIL)")
    m = metodo.upper()
    if m == "POST" and c == AUTH_PATH:
        return c
    if m == "GET" and c in GET_PERMITIDOS:
        return c
    raise OperacaoProibida(f"{m} {c} fora da allowlist do conector SIL (somente leitura)")


class _Throttle:
    def __init__(self, req_por_s: float, relogio: Callable[[], float], dormir: Callable[[float], None]):
        self.intervalo, self.relogio, self.dormir = 1.0 / req_por_s, relogio, dormir
        self.proximo = 0.0
        self.trava = threading.Lock()

    def espera(self) -> None:
        with self.trava:
            agora = self.relogio()
            if agora < self.proximo:
                self.dormir(self.proximo - agora)
                agora = self.proximo
            self.proximo = agora + self.intervalo


class ClienteSil:
    def __init__(self, config: ConfigSil, transport: httpx.BaseTransport | None = None,
                 relogio: Callable[[], float] = time.monotonic, dormir: Callable[[float], None] = time.sleep):
        if not config.habilitado:
            raise SilDesabilitado("Conector SIL desligado: defina SIL_BASE_URL, SIL_CLIENT_ID e SIL_CLIENT_SECRET.")
        self.config, self.relogio, self.dormir = config, relogio, dormir
        self._transport = transport
        self._bases = config.bases()
        self.ambiente: str | None = None  # "sandbox" ou "producao", escolhido na sondagem de autenticação
        self._http = self._cliente_http(self._bases[0][1])
        self._token: str | None = None
        self._expira_em = 0.0
        self._trava_token = threading.Lock()
        self._throttle = _Throttle(config.req_por_s, relogio, dormir)
        self.chamadas = {"auth": 0, "get": 0}

    def __repr__(self) -> str:
        return f"ClienteSil({self.config!r})"

    def _cliente_http(self, base_url: str) -> httpx.Client:
        return httpx.Client(base_url=base_url, timeout=self.config.timeout_s, transport=self._transport,
                            follow_redirects=False, headers={"Accept": "application/json"})

    def sondar(self) -> str:
        """Sondagem de autenticação (só leitura): tenta a homologação (sandbox) e, se ela falhar (rede, status ou
        credencial recusada), a produção. Fica no primeiro ambiente que autenticar. Devolve o ambiente."""
        ultimo: ErroSil | None = None
        for nome, url in self._bases:
            if self._http.base_url != httpx.URL(url):
                self._http.close()
                self._http = self._cliente_http(url)
            try:
                self._autentica()
            except RespostaAuthInvalida:
                raise  # o ambiente autenticou: nunca cai para o próximo (produção)
            except ErroSil as exc:
                log.warning("SIL: autenticação no ambiente %s falhou (%s)%s", nome, type(exc).__name__,
                            "; tentando o próximo" if (nome, url) != self._bases[-1] else "")
                ultimo = exc
                continue
            self.ambiente = nome
            log.info("SIL: autenticado no ambiente %s", nome)
            return nome
        raise ultimo  # type: ignore[misc]

    def fechar(self) -> None:
        self._http.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.fechar()

    # ---------- token ----------
    def _autentica(self) -> None:
        caminho = checa_operacao("POST", AUTH_PATH)
        form = {"grant_type": "client_credentials", "client_id": self.config.client_id,
                "client_secret": self.config.client_secret}
        if self.config.scope:
            form["scope"] = self.config.scope
        if self.config.password and self.config.password_campo:
            form[self.config.password_campo] = self.config.password
        self.chamadas["auth"] += 1
        r = self._envia("POST", caminho, data=form)
        if r.status_code in (400, 401, 403):
            raise FalhaCredencial("credencial da SIL recusada na autenticação")
        if r.status_code != 200:
            raise FalhaSil(f"autenticação da SIL respondeu {r.status_code}", r.status_code)
        try:
            j = r.json()
            token = j["access_token"]
            if not isinstance(token, str) or not token:
                raise ValueError
        except Exception:  # noqa: BLE001 - corpo nunca vai para a mensagem
            raise FalhaSil("resposta de autenticação da SIL inválida") from None
        try:
            expira = parse_expires_in(j.get("expires_in"))
        except (ValueError, TypeError):
            raise RespostaAuthInvalida("expires_in da autenticação da SIL em formato não reconhecido "
                                       "(esperado 600, \"600\" ou \"600s\")") from None
        self._token = str(token)
        self._expira_em = self.relogio() + max(expira - self.config.margem_token_s, 1.0)

    def token_valido(self) -> bool:
        return self._token is not None and self.relogio() < self._expira_em

    def _garante_token(self, forcar: bool = False) -> str:
        with self._trava_token:
            if self.ambiente is None:
                self.sondar()
            elif forcar or not self.token_valido():
                self._token = None
                self._autentica()
            return self._token  # type: ignore[return-value]

    # ---------- HTTP ----------
    def _envia(self, metodo: str, caminho: str, **kw) -> httpx.Response:
        """Uma requisição com retry/backoff em 429/5xx/timeout/conexão. Só loga método, caminho, status e tempo."""
        ultimo: Exception | None = None
        for tentativa in range(self.config.tentativas + 1):
            self._throttle.espera()
            t0 = self.relogio()
            try:
                r = self._http.request(metodo, caminho, **kw)
            except httpx.TimeoutException:  # a exceção do httpx traz a URL com a query: não é encadeada
                ultimo = FalhaSil("SIL não respondeu (timeout)")
                log.warning("SIL %s %s timeout (tentativa %d)", metodo, caminho, tentativa + 1)
            except httpx.TransportError as exc:
                ultimo = FalhaSil(f"erro de conexão com a SIL ({type(exc).__name__})")
                log.warning("SIL %s %s erro de conexão %s (tentativa %d)", metodo, caminho, type(exc).__name__,
                            tentativa + 1)
            else:
                log.info("SIL %s %s -> %d em %.0f ms (tentativa %d)", metodo, caminho, r.status_code,
                         (self.relogio() - t0) * 1000, tentativa + 1)
                if r.status_code != 429 and r.status_code < 500:
                    return r
                ultimo = FalhaSil(f"SIL respondeu {r.status_code}", r.status_code)
                if tentativa < self.config.tentativas:
                    self.dormir(self._espera(tentativa, r.headers.get("Retry-After")))
                continue
            if tentativa < self.config.tentativas:
                self.dormir(self._espera(tentativa, None))
        raise ultimo  # type: ignore[misc]

    def _espera(self, tentativa: int, retry_after: str | None) -> float:
        base = min(self.config.backoff_s * (2 ** tentativa), self.config.backoff_max_s)
        if retry_after:
            try:
                return min(max(float(retry_after), base), self.config.backoff_max_s)
            except ValueError:
                pass
        return base

    def get(self, caminho: str, params: dict[str, Any]) -> Any | None:
        """GET permitido. 404 -> None (não localizado). 401 renova o token uma vez; o segundo vira FalhaCredencial."""
        c = checa_operacao("GET", caminho)
        for renovou in (False, True):
            token = self._garante_token(forcar=renovou)
            self.chamadas["get"] += 1
            r = self._envia("GET", c, params=params, headers={"Authorization": f"Bearer {token}"})
            if r.status_code == 401:
                if renovou:
                    raise FalhaCredencial("SIL recusou o token renovado (401 duas vezes)")
                continue
            if r.status_code == 404:
                return None
            if r.status_code != 200:
                raise FalhaSil(f"SIL respondeu {r.status_code}", r.status_code)
            try:
                return r.json()
            except ValueError:
                raise FalhaSil("resposta da SIL não é JSON") from None
        raise FalhaCredencial("SIL recusou o token")  # pragma: no cover
