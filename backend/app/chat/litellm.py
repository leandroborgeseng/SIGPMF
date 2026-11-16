"""Cliente do LiteLLM (API compatível com OpenAI). A chave fica só no backend e só sai no cabeçalho Bearer."""
import logging

import httpx

from app.core.config import get_settings

log = logging.getLogger("sigpmf.chat")


class Indisponivel(Exception):
    """Sem configuração, timeout, rede ou resposta inutilizável. A mensagem ao usuário não leva stack nem corpo."""


class ToolsRecusadas(Exception):
    """O modelo recusou `tools` (HTTP 400). O chamador tenta de novo pedindo JSON estrito."""


def _credenciais() -> tuple[str, str, str, float]:
    s = get_settings()
    base = (s.litellm_base_url or "").strip().rstrip("/")
    modelo = (s.litellm_model or "").strip()
    chave = s.litellm_api_key.get_secret_value().strip() if s.litellm_api_key else ""
    if not base or not modelo or not chave:
        raise Indisponivel("nao_configurado")
    return base, modelo, chave, float(s.litellm_timeout_s)


def completar(mensagens: list[dict], tools: list[dict] | None = None) -> dict:
    """POST {base}/v1/chat/completions. Cabeçalho nosso: só `Authorization: Bearer`. Sem usuário, e-mail ou IP."""
    base, modelo, chave, timeout = _credenciais()
    corpo: dict = {"model": modelo, "messages": mensagens}
    if tools:
        corpo["tools"] = tools
        corpo["tool_choice"] = "auto"
    try:
        r = httpx.post(f"{base}/v1/chat/completions", json=corpo,
                       headers={"Authorization": f"Bearer {chave}"}, timeout=timeout)
    except httpx.HTTPError:
        log.warning("litellm indisponivel: conexao")
        raise Indisponivel("conexao") from None
    if r.status_code == 400 and tools:
        raise ToolsRecusadas()
    if r.status_code != 200:
        log.warning("litellm indisponivel: http %s", r.status_code)
        raise Indisponivel("http")
    try:
        dados = r.json()
    except ValueError:
        log.warning("litellm indisponivel: resposta")
        raise Indisponivel("resposta") from None
    if not isinstance(dados, dict):
        raise Indisponivel("resposta")
    return dados
