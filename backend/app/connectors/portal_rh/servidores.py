"""Portal da Transparência de Franca, página Servidores - Remuneração (RH1). Público, sem login, SOMENTE LEITURA.

- A página é JSF/PrimeFaces, sem API: a exportação é o mesmo caminho do navegador (ano, mês, pesquisar e o botão
  "Exportar para csv"), numa sessão nova por mês. O id do botão Pesquisar é lido da página (o JSF pode renumerá-lo).
- Só o host do portal (allowlist), sem seguir redirecionamento. O servidor não envia o certificado intermediário da
  cadeia TLS (o navegador contorna, o Python não): o intermediário público da Certum vai junto, com a verificação
  ligada (certum_dv_tls_g2_r39.pem, validade 2039).
- Mês sem dados: o portal devolve o CSV só com o cabeçalho, ou a página sem o arquivo. Os dois viram lista vazia.
- Traz nome e matrícula: guardados na prata para o cruzamento com o Senior; mensagens e logs só têm contagens.
"""
import csv
import hashlib
import io
import os
import re
import ssl
from dataclasses import dataclass
from urllib.parse import urlparse

import certifi
import httpx

SISTEMA = "PORTAL_RH"
HOST_PERMITIDO = "www.franca.sp.gov.br"
URL_PADRAO = f"https://{HOST_PERMITIDO}/portal-transparencia/paginas/publica/servidores.xhtml"
INTERMEDIARIO = os.path.join(os.path.dirname(__file__), "certum_dv_tls_g2_r39.pem")
COLUNAS = ("Situação", "Matrícula", "Nome", "Cargo", "Salário base", "Total vencimentos", "Total descontos", "Total líquido",
           "Função inativa", "Data de admissão", "Secretaria", "Jornada", "Referência", "Data de afastamento", "Data de demissão")
MES_13 = 13  # "Décimo Terceiro" na lista de meses do portal
_VS = re.compile(r'javax\.faces\.ViewState[^>]*?value="([^"]+)"')
_VS_AJAX = re.compile(r'id="[^"]*javax\.faces\.ViewState[^"]*"><!\[CDATA\[(.*?)\]\]>')
_BOTAO = re.compile(r'<button id="(formFiltro:j_idt\d+)"')
AJAX = {"Faces-Request": "partial/ajax", "X-Requested-With": "XMLHttpRequest"}


class ErroPortal(RuntimeError):
    """Falha ao falar com o portal ou arquivo fora do formato. Mensagem segura (sem conteúdo de linha)."""


@dataclass(frozen=True)
class ConfigPortalRh:
    url: str
    ano_inicial: int
    pausa_s: float

    @classmethod
    def do_ambiente(cls) -> "ConfigPortalRh":
        """SIGPMF_SRC_RH_URL / _ANO_INICIAL (padrão 2012, o primeiro do portal) / _PAUSA_S (padrão 1 s entre meses)."""
        ano = os.environ.get("SIGPMF_SRC_RH_ANO_INICIAL", "").strip()
        try:
            pausa = max(float(os.environ.get("SIGPMF_SRC_RH_PAUSA_S", "1")), 0.0)
        except ValueError:
            pausa = 1.0
        return cls(url=(os.environ.get("SIGPMF_SRC_RH_URL") or URL_PADRAO).strip(),
                   ano_inicial=int(ano) if ano.isdigit() and int(ano) >= 2012 else 2012, pausa_s=pausa)


def confere_host(url: str) -> None:
    p = urlparse(url)
    if p.scheme != "https" or p.hostname != HOST_PERMITIDO:
        raise PermissionError("Conector de RH: só o Portal da Transparência de Franca é permitido.")


def cliente_http() -> httpx.Client:
    ctx = ssl.create_default_context(cafile=certifi.where())
    ctx.load_verify_locations(INTERMEDIARIO)
    return httpx.Client(verify=ctx, timeout=httpx.Timeout(120.0, connect=20.0), follow_redirects=False,
                        headers={"User-Agent": "SIGPMF/RH (somente leitura)"})


def _view_state(texto: str, atual: str | None = None) -> str:
    m = _VS.search(texto) or _VS_AJAX.search(texto)
    if m:
        return m.group(1)
    if atual is None:
        raise ErroPortal("A página do portal mudou: não achei o estado do formulário.")
    return atual


def baixa_mes(cliente: httpx.Client, url: str, ano: int, mes: int) -> bytes | None:
    """CSV do mês (1 a 12, ou 13 = décimo terceiro). None = o portal não tem o mês."""
    confere_host(url)
    try:
        r = cliente.get(url)
        r.raise_for_status()
        botao = _BOTAO.search(r.text)
        if not botao:
            raise ErroPortal("A página do portal mudou: não achei o botão Pesquisar.")
        vs = _view_state(r.text)
        form = {"formFiltro": "formFiltro", "formFiltro:cbmAno:cmbInput_focus": "", "formFiltro:cbmAno:cmbInput_input": str(ano),
                "formFiltro:cmbMes:cmbInput_focus": "", "formFiltro:cmbMes:cmbInput_input": "", "formFiltro:txtNome:txtInput": ""}

        def ajax(origem: str, processa: str, atualiza: str, evento: bool) -> httpx.Response:
            nonlocal vs
            d = {**form, "javax.faces.ViewState": vs, "javax.faces.partial.ajax": "true", "javax.faces.source": origem,
                 "javax.faces.partial.execute": processa, "javax.faces.partial.render": atualiza}
            if evento:
                d["javax.faces.behavior.event"] = d["javax.faces.partial.event"] = "change"
            else:
                d[origem] = origem
            resp = cliente.post(url, data=d, headers=AJAX)
            resp.raise_for_status()
            if "<error>" in resp.text:
                raise ErroPortal(f"O portal recusou a consulta de {mes:02d}/{ano}.")
            vs = _view_state(resp.text, vs)
            return resp
        ajax("formFiltro:cbmAno:cmbInput", "formFiltro:cbmAno:cmbInput", "formFiltro:outMes formFiltro:pnlCargo", True)
        form["formFiltro:cmbMes:cmbInput_input"] = str(mes)
        ajax("formFiltro:cmbMes:cmbInput", "formFiltro:pnlAnoMes", "formFiltro:pnlCargo", True)
        ajax(botao.group(1), "@all", "formFiltro", False)
        r = cliente.post(url, data={**form, "javax.faces.ViewState": vs, "formFiltro:csv": "formFiltro:csv"})
        r.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise ErroPortal(f"O portal respondeu HTTP {exc.response.status_code}.") from exc
    except httpx.TransportError as exc:
        raise ErroPortal(f"Sem resposta do portal ({type(exc).__name__}).") from exc
    return r.content if "csv" in r.headers.get("content-type", "") else None


def le_csv(conteudo: bytes) -> list[dict]:
    """Linhas do CSV, com o cabeçalho conferido. Cabeçalho diferente ou linha sem matrícula = arquivo recusado."""
    linhas = csv.reader(io.StringIO(conteudo.decode("utf-8-sig", errors="replace")))
    cab = tuple(c.strip() for c in next(linhas, []))
    if cab != COLUNAS:
        raise ErroPortal("O CSV do portal mudou de formato (cabeçalho diferente do esperado).")
    out = []
    for n, v in enumerate(linhas, start=2):
        if not v:
            continue
        if len(v) != len(COLUNAS):
            raise ErroPortal(f"Linha {n} do CSV com {len(v)} colunas (esperado {len(COLUNAS)}).")
        r = dict(zip(COLUNAS, (x.strip() for x in v), strict=True))
        if not r["Matrícula"]:
            raise ErroPortal(f"Linha {n} do CSV sem matrícula.")
        out.append(r)
    return out


def sha256(conteudo: bytes) -> str:
    return hashlib.sha256(conteudo).hexdigest()
