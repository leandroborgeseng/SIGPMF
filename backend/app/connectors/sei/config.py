"""Configuração do conector SEI, só por variável de ambiente (SEI7). Nenhum host no código, nenhum valor padrão sensível.

Oficial (padrão):       SIGPMF_SRC_SEI_BASE_PROCESSOS / _BASE_DOCUMENTOS / _BASE_PARAMETROS / _IDP_URL
Homologação (só com SIGPMF_SRC_SEI_USAR_HOMOLOGACAO=true):
                        SIGPMF_SRC_SEI_HML_BASE_PROCESSOS / _HML_BASE_DOCUMENTOS / _HML_BASE_PARAMETROS / _HML_IDP_URL
Caminho do token:       SIGPMF_SRC_SEI_IDP_TOKEN_PATH (oficial) / _HML_IDP_TOKEN_PATH, somado ao _IDP_URL (base do IdP)
Comuns:                 SIGPMF_SRC_SEI_IDP_CLIENT_ID, _IDP_CLIENT_SECRET, _SIGLA_SISTEMA, _IDENTIFICACAO_SERVICO,
                        _ID_UNIDADE, _REGIONAL
Nunca há fallback entre os ambientes: com a flag desligada, só as variáveis oficiais valem; com ela ligada, só as de
homologação. Uma URL "oficial" com cara de homologação (hml) deixa o conector inerte.
"""
import math
import os
import re
from dataclasses import dataclass

PREFIXO = "SIGPMF_SRC_SEI_"
SERVICOS = ("processos", "documentos", "parametros")
OFICIAL, HOMOLOGACAO = "oficial", "homologacao"
_CAMINHO_TOKEN = re.compile(r"^/?[A-Za-z0-9._~\-]+(/[A-Za-z0-9._~\-]+)*$")
_HML = re.compile(r"(^|[.\-/])hml([.\-/]|$)", re.IGNORECASE)


def _env(nome: str) -> str | None:
    v = os.environ.get(PREFIXO + nome)
    return v.strip() if v and v.strip() else None


def _url(nome: str) -> str | None:
    v = _env(nome)
    return v.rstrip("/") if v else None


def _verdade(nome: str) -> bool:
    return (_env(nome) or "").lower() in ("1", "true", "sim", "yes")


def _num(nome: str, padrao: float, minimo: float, maximo: float) -> float:
    try:
        x = float(_env(nome) or padrao)
    except ValueError:
        x = padrao
    if not math.isfinite(x):  # nan/inf: float() aceita, min/max não resolvem (D1)
        x = padrao
    return min(max(x, minimo), maximo)


TETO_PAGINAS = 1  # decisão do TL: travado em 1 até o Leandro validar a amostra oficial
_log_cfg = __import__("logging").getLogger(__name__)


def _teto_paginas(v: int) -> int:
    if v > TETO_PAGINAS:
        _log_cfg.warning("SIGPMF_SRC_SEI_MAX_PAGINAS_POR_EXECUCAO=%s acima do teto; usando %s", v, TETO_PAGINAS)
        return TETO_PAGINAS
    return v


@dataclass(repr=False)
class ConfigSei:
    ambiente: str                         # "oficial" ou "homologacao" (ambiente_origem)
    bases: dict[str, str | None]          # processos/documentos/parametros
    idp_url: str | None                   # base do IdP (sem caminho)
    idp_token_path: str | None            # caminho do token, só por env (ainda a confirmar)
    client_id: str | None
    client_secret: str | None
    sigla_sistema: str | None
    identificacao_servico: str | None
    id_unidade: str | None
    regional: str | None
    timeout_s: float = 15.0
    tentativas: int = 2
    backoff_s: float = 0.5
    margem_token_s: float = 30.0
    limite_amostra: int = 50             # SEI3: limit <= 50
    detalhes_amostra: int = 5            # SEI3: até 5 detalhes
    max_paginas: int = 1                 # páginas por execução até o Leandro validar a amostra
    carga_liberada: bool = False         # SEI3: carga além da amostra só depois da validação do Leandro

    @classmethod
    def do_ambiente(cls) -> "ConfigSei":
        hml = _verdade("USAR_HOMOLOGACAO")
        p = "HML_" if hml else ""
        return cls(ambiente=HOMOLOGACAO if hml else OFICIAL,
                   bases={s: _url(f"{p}BASE_{s.upper()}") for s in SERVICOS}, idp_url=_url(f"{p}IDP_URL"),
                   idp_token_path=_env(f"{p}IDP_TOKEN_PATH"),
                   client_id=_env("IDP_CLIENT_ID"), client_secret=_env("IDP_CLIENT_SECRET"),
                   sigla_sistema=_env("SIGLA_SISTEMA"), identificacao_servico=_env("IDENTIFICACAO_SERVICO"),
                   id_unidade=_env("ID_UNIDADE"), regional=_env("REGIONAL"),
                   timeout_s=_num("TIMEOUT_S", 15, 1, 120), tentativas=int(_num("TENTATIVAS", 2, 0, 5)),
                   limite_amostra=int(_num("LIMITE_AMOSTRA", 50, 1, 50)),
                   max_paginas=_teto_paginas(int(_num("MAX_PAGINAS_POR_EXECUCAO", 1, 1, 10000))),
                   carga_liberada=_verdade("CARGA_LIBERADA"))

    def pendencias(self) -> list[str]:
        """Nomes (nunca valores) do que falta para o conector sair do estado inerte."""
        p = "HML_" if self.ambiente == HOMOLOGACAO else ""
        falta = [f"{PREFIXO}{p}BASE_{s.upper()}" for s in ("processos", "parametros") if not self.bases.get(s)]
        if not self.idp_url:
            falta.append(f"{PREFIXO}{p}IDP_URL")
        if not self.idp_token_path:
            falta.append(f"{PREFIXO}{p}IDP_TOKEN_PATH")
        elif not _CAMINHO_TOKEN.match(self.idp_token_path) or ".." in self.idp_token_path:
            falta.append(f"{PREFIXO}{p}IDP_TOKEN_PATH inválido (só caminho relativo ao IdP, ex.: /auth/.../token)")
        for campo, nome in (("client_id", "IDP_CLIENT_ID"), ("client_secret", "IDP_CLIENT_SECRET"),
                            ("sigla_sistema", "SIGLA_SISTEMA"), ("identificacao_servico", "IDENTIFICACAO_SERVICO"),
                            ("id_unidade", "ID_UNIDADE"), ("regional", "REGIONAL")):
            if not getattr(self, campo):
                falta.append(PREFIXO + nome)
        if self.ambiente == OFICIAL:
            urls = [u for u in (*self.bases.values(), self.idp_url) if u]
            if any(_HML.search(u.split("//", 1)[-1]) for u in urls):
                falta.append("URL oficial aponta para homologação (ligue SIGPMF_SRC_SEI_USAR_HOMOLOGACAO=true)")
        return falta

    @property
    def idp_token_url(self) -> str | None:
        """URL do token = base do IdP + caminho configurado. Nunca muda de host."""
        if not self.idp_url or not self.idp_token_path:
            return None
        return self.idp_url.rstrip("/") + "/" + self.idp_token_path.lstrip("/")

    @property
    def habilitado(self) -> bool:
        return not self.pendencias()

    def __repr__(self) -> str:  # nunca expõe URL, id, segredo nem identificação do serviço
        return (f"ConfigSei(ambiente={self.ambiente}, bases=***, idp_url=***, client_id=***, client_secret=***, "
                f"identificacao_servico=***, habilitado={self.habilitado})")

    __str__ = __repr__

    def resumo_publico(self) -> dict:
        return {"habilitado": self.habilitado, "ambiente_origem": self.ambiente, "pendencias": self.pendencias(),
                "bases_configuradas": {s: bool(u) for s, u in self.bases.items()},
                "idp_configurado": bool(self.idp_url), "carga_liberada": self.carga_liberada,
                "limite_amostra": self.limite_amostra, "max_paginas_por_execucao": self.max_paginas}
