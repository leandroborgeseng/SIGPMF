"""Configuração do conector SIL, só por variável de ambiente (SIL1). Nada tem valor padrão sensível."""
import os
from dataclasses import dataclass, field


def _env(nome: str) -> str | None:
    v = os.environ.get(nome)
    return v.strip() if v and v.strip() else None


def _num(nome: str, padrao: float, minimo: float, maximo: float) -> float:
    v = _env(nome)
    try:
        x = float(v) if v is not None else padrao
    except ValueError:
        x = padrao
    return min(max(x, minimo), maximo)


def _ritmo_legado() -> float:
    """SIL_INTERVALO_MS (conector antigo) continua valendo como ritmo quando SIL_REQ_POR_S não está definido."""
    ms = _num("SIL_INTERVALO_MS", 0, 0, 10000)
    return 1000.0 / ms if ms > 0 else 2.0


@dataclass(repr=False)
class ConfigSil:
    base_url: str | None          # produção
    client_id: str | None
    client_secret: str | None = field(default=None)
    scope: str | None = None
    # A SIL mandou também uma "senha" que o fluxo client_credentials não usa. Só vai no formulário de autenticação se
    # SIL_PASSWORD_CAMPO disser o nome do campo (a confirmar com a SIL); sem isso, é ignorada.
    password: str | None = field(default=None)
    password_campo: str | None = None
    timeout_s: float = 10.0
    tentativas: int = 3          # tentativas extras em 429/5xx/timeout (backoff exponencial)
    backoff_s: float = 0.5
    backoff_max_s: float = 30.0
    req_por_s: float = 2.0       # SIL5: throttle (padrão 2 req/s)
    margem_token_s: float = 60.0  # SIL2: renova antes de expirar (expires_in 600s -> renova aos 540s)
    falhas_seguidas_max: int = 3  # SIL5: para o lote depois de 3 falhas seguidas
    # TL 27/10: a sondagem de autenticação tenta primeiro a homologação (sandbox) e só depois a produção.
    base_url_sandbox: str | None = None
    # Unificação (TL 28/10): carga completa, agenda e amostra genérica leem no máximo N lotes do consultarlote por
    # execução (padrão 1) até o Leandro validar a primeira amostra. SIL_MAX_LOTES_POR_EXECUCAO muda o teto.
    max_lotes_por_execucao: int = 1

    def bases(self) -> list[tuple[str, str]]:
        """Ordem da sondagem: [("sandbox", url), ("producao", url)], só as configuradas."""
        return [(n, u) for n, u in (("sandbox", self.base_url_sandbox), ("producao", self.base_url)) if u]

    @classmethod
    def do_ambiente(cls, _settings=None) -> "ConfigSil":  # _settings: compatível com o ConfigSil antigo do conector
        return cls(base_url=(_env("SIL_BASE_URL") or "").rstrip("/") or None, client_id=_env("SIL_CLIENT_ID"),
                   client_secret=_env("SIL_CLIENT_SECRET"), scope=_env("SIL_SCOPE"), password=_env("SIL_PASSWORD"),
                   password_campo=_env("SIL_PASSWORD_CAMPO"),
                   timeout_s=_num("SIL_TIMEOUT_S", _num("SIL_TIMEOUT", 10.0, 1.0, 120.0), 1.0, 120.0),
                   tentativas=int(_num("SIL_TENTATIVAS", 3, 0, 10)),
                   req_por_s=_num("SIL_REQ_POR_S", _ritmo_legado(), 0.1, 20.0),
                   max_lotes_por_execucao=int(_num("SIL_MAX_LOTES_POR_EXECUCAO", 1, 1, 100000)),
                   base_url_sandbox=(_env("SIL_BASE_URL_SANDBOX") or "").rstrip("/") or None)

    @property
    def habilitado(self) -> bool:
        """Desligado a menos que haja uma URL (sandbox ou produção), client_id e client_secret."""
        def ok(v: str | None) -> bool:  # SIL14: vazio ou só espaços conta como ausente
            return bool(v and v.strip())
        return (ok(self.base_url) or ok(self.base_url_sandbox)) and ok(self.client_id) and ok(self.client_secret)

    def configurado(self) -> bool:  # nome do ConfigSil antigo (conector.py); SIL14: só espaços = não configurado
        return self.habilitado

    def __repr__(self) -> str:  # nunca expõe id, segredo, senha nem a URL (logs, tracebacks)
        return (f"ConfigSil(base_url={'***' if self.base_url else None}, "
                f"base_url_sandbox={'***' if self.base_url_sandbox else None}, client_id=***, client_secret=***, "
                f"scope={'***' if self.scope else None}, password=***, timeout_s={self.timeout_s}, "
                f"tentativas={self.tentativas}, req_por_s={self.req_por_s}, "
                f"max_lotes_por_execucao={self.max_lotes_por_execucao})")

    __str__ = __repr__

    def resumo_publico(self) -> dict:
        """Para a tela/status: só se está configurado, nunca os valores."""
        return {"habilitado": self.habilitado, "base_url_configurada": bool(self.base_url),
                "sandbox_configurado": bool(self.base_url_sandbox),
                "client_id_configurado": bool(self.client_id), "client_secret_configurado": bool(self.client_secret),
                "scope_configurado": bool(self.scope), "timeout_s": self.timeout_s, "req_por_s": self.req_por_s,
                "max_lotes_por_execucao": self.max_lotes_por_execucao}
