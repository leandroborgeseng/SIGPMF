"""Cliente HTTP do SEI (SEI1, SEI2, SEI7). Toda chamada passa por checa_operacao ANTES de abrir conexão.

- Allowlist v1 (só GET): processos: /processos, /processos/{protocolo}, /processos/{protocolo}/documentos,
  /andamentos/completo, /processos/tipos; parametros: /unidades, /assuntos, /series. O serviço de documentos não tem
  nenhuma rota liberada na v1 (conteúdo e anexos são base64 e podem trazer qualquer dado).
- Qualquer outro método (escrita) ou rota (/emails, /arquivos, /contatos, /usuarios, /{cpf}/atribuicao,
  /conteudo, /anexos, ...) levanta OperacaoProibida. O único POST é o do token no IdP.
- Parâmetros de query: só os nomes previstos para cada rota (nada de cpf/nome/usuário na URL). Protocolo na rota só
  no formato de protocolo SEI (um CPF de 11 dígitos é recusado).
- Token Bearer do IdP SP (client_credentials) só em memória; 401 renova UMA vez; o segundo 401 vira FalhaCredencial.
- Cabeçalhos fixos: xTraceIdSP (gerado por chamada), xSiglaSistema, xIdentificacaoServico, xIdUnidade (exceto
  /unidades) e xRegional, todos das variáveis de ambiente.
- Log: só ambiente, método, NOME da rota, status e tentativa. Nunca URL, protocolo, query, corpo, token ou segredo.
"""
import logging
import re
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, unquote

import httpx

from app.connectors.sei.config import ConfigSei

log = logging.getLogger("sigpmf.conector.sei")
for _nome in ("httpx", "httpcore"):  # logam a URL completa em INFO/DEBUG
    logging.getLogger(_nome).setLevel(logging.WARNING)

_PROTOCOLO = re.compile(r"^[0-9][0-9./\-]{3,40}$")
_SO_DIGITOS_11 = re.compile(r"^\d{11}$")


@dataclass(frozen=True)
class Rota:
    nome: str
    servico: str
    modelo: str                       # caminho com {protocolo}
    params: frozenset[str] = frozenset()
    com_unidade: bool = True          # xIdUnidade (a doc não pede em /unidades)


_PAG = frozenset({"start", "limit"})
ROTAS: dict[str, Rota] = {r.nome: r for r in (
    Rota("processos", "processos", "/processos", _PAG | {"sinRetornarUltimaMovimentacao", "sinRetornarStatus"}),
    Rota("processo", "processos", "/processos/{protocolo}",
         frozenset({"sinRetornarAssuntos", "sinRetornarInteressados", "sinRetornarAndamentoGeracao",
                    "sinRetornarUltimoAndamento"})),
    Rota("documentos_processo", "processos", "/processos/{protocolo}/documentos", _PAG),
    Rota("andamentos", "processos", "/andamentos/completo",
         _PAG | {"dataInicio", "dataFim", "retornaDescricaoTipoProcesso"}),
    Rota("tipos", "processos", "/processos/tipos"),
    Rota("unidades", "parametros", "/unidades", _PAG, com_unidade=False),
    Rota("assuntos", "parametros", "/assuntos", _PAG),
    Rota("series", "parametros", "/series"),
)}
# Palavras que nunca podem aparecer como segmento de caminho (qualquer método).
SEGMENTOS_PROIBIDOS = frozenset({"contatos", "usuarios", "atribuicao", "conteudo", "anexos", "emails", "arquivos",
                                 "replica-usuario", "fila", "cancelamento", "blocos", "conclusao", "envio",
                                 "sobrestamento", "andamento", "relacionamento", "reabertura", "anexacao", "bloqueio",
                                 "prazo", "marcador", "nivelacesso", "recebimento", "disponibilizacaoacessoexterno",
                                 "individual", "marcadores", "textos-padrao"})
# Segundo segmento de /processos/X que é rota da API, não protocolo.
_NAO_PROTOCOLO = frozenset({"andamentos", "individual", "tipo", "tipos", "marcadores"})


class ErroSei(RuntimeError):
    """Mensagem sempre segura: sem URL, protocolo, token, segredo ou dado pessoal."""


class SeiDesabilitado(ErroSei):
    pass


class OperacaoProibida(ErroSei):
    pass


class FalhaCredencial(ErroSei):
    pass


class FalhaSei(ErroSei):
    def __init__(self, motivo: str, status: int | None = None):
        super().__init__(motivo)
        self.status = status


def _segmentos(caminho: str) -> list[str]:
    if not caminho.startswith("/") or "?" in caminho or "#" in caminho or "\\" in caminho:
        raise OperacaoProibida("caminho inválido para o SEI")
    return [p for p in caminho.split("/") if p]


def checa_operacao(metodo: str, servico: str, caminho: str) -> Rota:
    """Allowlist (SEI1). `caminho` é o caminho já montado (protocolo codificado com %2F). Devolve a rota permitida."""
    segs = _segmentos(caminho)
    dec = [unquote(s) for s in segs]
    if any(s in (".", "..") for s in dec):
        raise OperacaoProibida("caminho inválido para o SEI")
    if any(s.lower() in SEGMENTOS_PROIBIDOS for s in dec):
        raise OperacaoProibida("rota proibida no conector SEI (escrita, dado pessoal ou conteúdo de documento)")
    if metodo.upper() != "GET":
        raise OperacaoProibida(f"{metodo.upper()} não é permitido no conector SEI (somente leitura)")
    for r in ROTAS.values():
        if r.servico != servico:
            continue
        modelo = [p for p in r.modelo.split("/") if p]
        if len(modelo) != len(segs):
            continue
        ok = True
        for m, s, d in zip(modelo, segs, dec, strict=True):
            if m == "{protocolo}":
                if "/" in s or d.lower() in _NAO_PROTOCOLO or not _PROTOCOLO.match(d) or _SO_DIGITOS_11.match(d):
                    ok = False
            elif m != s:
                ok = False
        if ok:
            return r
    raise OperacaoProibida(f"GET fora da allowlist do conector SEI ({servico})")


def monta_caminho(rota: Rota, protocolo: str | None = None) -> str:
    if "{protocolo}" in rota.modelo:
        if not protocolo:
            raise OperacaoProibida("protocolo obrigatório")
        return rota.modelo.replace("{protocolo}", quote(str(protocolo).strip(), safe=""))
    return rota.modelo


class ClienteSei:
    def __init__(self, config: ConfigSei, transport: httpx.BaseTransport | None = None,
                 relogio: Callable[[], float] = time.monotonic, dormir: Callable[[float], None] = time.sleep):
        if not config.habilitado:
            raise SeiDesabilitado("Conector SEI inerte: variáveis ausentes (" + ", ".join(config.pendencias()) + ").")
        self.config, self.relogio, self.dormir = config, relogio, dormir
        self.ambiente = config.ambiente
        self._http = httpx.Client(timeout=config.timeout_s, transport=transport, follow_redirects=False,
                                  headers={"Accept": "application/json"})
        self._token: str | None = None
        self._expira = 0.0
        self.chamadas = {"token": 0, "get": 0}

    def __repr__(self) -> str:
        return f"ClienteSei({self.config!r})"

    def fechar(self) -> None:
        self._http.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.fechar()

    # ---------------------------------------------------------------- token (SEI2)
    def _autentica(self) -> None:
        self.chamadas["token"] += 1
        form = {"grant_type": "client_credentials", "client_id": self.config.client_id,
                "client_secret": self.config.client_secret}
        r = self._envia("POST", self.config.idp_url, "token", data=form)
        if r.status_code in (400, 401, 403):
            raise FalhaCredencial(f"IdP SP recusou a credencial do SEI ({self.ambiente})")
        if r.status_code != 200:
            raise FalhaSei(f"IdP SP respondeu {r.status_code}", r.status_code)
        try:
            j = r.json()
            tok = j["access_token"]
            exp = float(j.get("expires_in") or 300)
            if not isinstance(tok, str) or not tok or exp <= 0:
                raise ValueError
        except Exception:  # noqa: BLE001 - corpo nunca vai para mensagem
            raise FalhaSei("resposta do IdP SP inválida") from None
        self._token = tok
        self._expira = self.relogio() + max(exp - self.config.margem_token_s, 1.0)

    def _garante_token(self, forcar: bool) -> str:
        if forcar or self._token is None or self.relogio() >= self._expira:
            self._token = None
            self._autentica()
        return self._token  # type: ignore[return-value]

    # ---------------------------------------------------------------- HTTP
    def _envia(self, metodo: str, url: str, nome: str, **kw) -> httpx.Response:
        ultimo: ErroSei | None = None
        for t in range(self.config.tentativas + 1):
            try:
                r = self._http.request(metodo, url, **kw)
            except httpx.TimeoutException:  # a exceção do httpx traz a URL: não é encadeada
                ultimo = FalhaSei("SEI não respondeu (timeout)")
                log.info("SEI[%s] %s %s timeout (tentativa %d)", self.ambiente, metodo, nome, t + 1)
            except httpx.TransportError as exc:
                ultimo = FalhaSei(f"erro de conexão com o SEI ({type(exc).__name__})")
                log.info("SEI[%s] %s %s erro de conexão (tentativa %d)", self.ambiente, metodo, nome, t + 1)
            else:
                log.info("SEI[%s] %s %s -> %d (tentativa %d)", self.ambiente, metodo, nome, r.status_code, t + 1)
                if r.status_code != 429 and r.status_code < 500:
                    return r
                ultimo = FalhaSei(f"SEI respondeu {r.status_code}", r.status_code)
            if t < self.config.tentativas:
                self.dormir(self.config.backoff_s * (2 ** t))
        raise ultimo  # type: ignore[misc]

    def cabecalhos(self, rota: Rota, token: str) -> dict[str, str]:
        h = {"Authorization": f"Bearer {token}", "xTraceIdSP": uuid.uuid4().hex,
             "xSiglaSistema": self.config.sigla_sistema, "xIdentificacaoServico": self.config.identificacao_servico,
             "xRegional": self.config.regional}
        if rota.com_unidade:
            h["xIdUnidade"] = self.config.id_unidade
        return h  # type: ignore[return-value]

    def get(self, nome_rota: str, protocolo: str | None = None, params: dict[str, Any] | None = None) -> Any | None:
        """GET permitido. 404 -> None. 401 renova o token uma vez."""
        rota = ROTAS.get(nome_rota)
        if rota is None:
            raise OperacaoProibida("rota fora da allowlist do conector SEI")
        caminho = monta_caminho(rota, protocolo)
        checa_operacao("GET", rota.servico, caminho)
        params = dict(params or {})
        extra = set(params) - rota.params
        if extra:
            raise OperacaoProibida(f"parâmetro fora da allowlist na rota {rota.nome}")
        url = self.config.bases[rota.servico] + caminho  # type: ignore[operator]
        for renovou in (False, True):
            tok = self._garante_token(renovou)
            self.chamadas["get"] += 1
            r = self._envia("GET", url, rota.nome, params=params, headers=self.cabecalhos(rota, tok))
            if r.status_code == 401:
                if renovou:
                    raise FalhaCredencial("SEI recusou o token renovado (401 duas vezes)")
                continue
            if r.status_code == 404:
                return None
            if r.status_code != 200:
                raise FalhaSei(f"SEI respondeu {r.status_code} em {rota.nome}", r.status_code)
            try:
                return r.json()
            except ValueError:
                raise FalhaSei(f"resposta do SEI não é JSON em {rota.nome}") from None
        raise FalhaCredencial("SEI recusou o token")  # pragma: no cover
