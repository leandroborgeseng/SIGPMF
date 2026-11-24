"""Arquivos do AUDESP no portal de transparência do TCE-SP: contrato, download e leitura (AU1/AU2). SOMENTE LEITURA.

- Três arquivos por município e ano, cada um com o ano inteiro até o último mês publicado (o novo substitui o anterior):
  receitas, despesas e restos a pagar. URLs conferidas em 03/11/2026 (2024–2026, HTTP 200, application/zip).
- Só baixa quando mudou: HEAD por arquivo; `Last-Modified` + `Content-Length` formam a assinatura guardada no controle;
  baixado, o SHA-256 do CSV (não do zip) diz se o conteúdo mudou mesmo.
- Só o host do TCE (allowlist), timeout, 3 tentativas. Zip com exatamente um .csv, teto do zip (100 MB) e do CSV
  descompactado (500 MB), lido em streaming direto do zip (nada no disco).
- O tipo do arquivo vem do cabeçalho (não do nome): cabeçalho diferente = arquivo recusado, sem carregar nada.
- `historico_despesa` não entra (texto livre, pode ter nome de pessoa). Mensagens sem URL de credencial (não há) e
  sem conteúdo de linha.
"""
import csv
import hashlib
import io
import os
import time
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from urllib.parse import urlparse

import httpx

SISTEMA = "AUDESP"
HOST_PERMITIDO = "transparencia.tce.sp.gov.br"
BASE_PADRAO = f"https://{HOST_PERMITIDO}/sites/default/files"
MAX_ZIP = 100 * 1024 * 1024
MAX_CSV = 500 * 1024 * 1024
TENTATIVAS = 3
ENCODING = "iso-8859-1"

COLUNAS = {
    "despesas": ("id_despesa_detalhe", "ano_exercicio", "ds_municipio", "ds_orgao", "mes_referencia", "mes_ref_extenso",
                 "tp_despesa", "nr_empenho", "identificador_despesa", "ds_despesa", "dt_emissao_despesa", "vl_despesa",
                 "ds_funcao_governo", "ds_subfuncao_governo", "cd_programa", "ds_programa", "cd_acao", "ds_acao",
                 "ds_fonte_recurso", "ds_cd_aplicacao_fixo", "ds_modalidade_lic", "ds_elemento", "historico_despesa"),
    "receitas": ("id_rec_arrec_detalhe", "ano_exercicio", "ds_municipio", "ds_orgao", "mes_referencia", "mes_ref_extenso",
                 "ds_poder", "ds_fonte_recurso", "ds_cd_aplicacao_fixo", "ds_cd_aplicacao_variavel", "ds_categoria",
                 "ds_subcategoria", "ds_fonte", "ds_d1", "ds_dd2", "ds_d3", "ds_tipo", "vl_arrecadacao"),
    "restos_a_pagar": ("Município", "Órgão", "UO", "UE", "Mod. de Licitação", "Nr. Licitação", "Função de Governo",
                       "Subfunção de Governo", "Programa", "Ação", "Fonte de Recurso", "Cód. Aplicação - Fixo",
                       "Cód. Aplicação - Variável", "Categoria", "Grupo", "Modalidade", "Elemento", "Subelemento",
                       "ID Credor", "Nome do Credor", "Nr. Empenho", "Ano Empenho", "Mes Balancete", "Ano Balancete",
                       "Dt. Referência", "Vl. Inscrito - RP Proc", "Vl. Saldo Inicial - RP Proc", "Vl. Cancelado - RP Proc",
                       "Vl. Pago - RP Proc", "Vl. Estorno Pago - RP Proc", "Vl. Inscrito - RP Não Proc",
                       "Vl. Saldo Inicial - RP Não Proc", "Vl. Liquidado - RP Não Proc",
                       "Vl. Estorno Liquidado - RP Não Proc", "Vl. Cancelado - RP Não Proc", "Vl. Pago - RP Não Proc",
                       "Vl. Estorno Pago - RP Não Proc"),
}
FORA_DA_BRONZE = {"despesas": ("historico_despesa",)}
CHAVE = {"despesas": "id_despesa_detalhe", "receitas": "id_rec_arrec_detalhe", "restos_a_pagar": "_id"}
# Restos a pagar não tem id e o empenho se repete no mesmo balancete (2.942 casos em 2026) sem linha inteira repetida:
# a chave é o hash da linha completa. O CPF do credor pessoa física (ID Credor = "CPF:<11 dígitos>") sai antes da
# bronze: o painel não precisa dele (minimização, LGPD); fica só a marca "CPF".
CAMINHOS = {"receitas": "csv/receitas-{m}-{ano}.zip", "despesas": "csv/despesas-{m}-{ano}.zip",
            "restos_a_pagar": "dados/planilhas/restos/{m}-{ano}.zip"}
TIPOS = tuple(CAMINHOS)


class ArquivoInvalido(ValueError):
    """Arquivo que não segue o contrato do AUDESP (cabeçalho, zip, tamanho). Mensagem segura para a tela."""


class ErroDownload(RuntimeError):
    """Falha de rede ou HTTP ao falar com o portal do TCE (depois das tentativas)."""


@dataclass(frozen=True)
class ConfigAudesp:
    base: str
    municipio: str
    anos: tuple[int, ...]

    @classmethod
    def do_ambiente(cls, hoje: date | None = None) -> "ConfigAudesp":
        """SIGPMF_SRC_AUDESP_BASE / _MUNICIPIO / _ANOS (lista por vírgula; padrão: os 3 últimos anos, com o atual)."""
        ano = (hoje or date.today()).year
        bruto = (os.environ.get("SIGPMF_SRC_AUDESP_ANOS") or "").strip()
        anos = tuple(sorted({int(x) for x in bruto.split(",") if x.strip().isdigit()})) if bruto else ()
        if not anos or any(a < 2014 or a > ano for a in anos):
            anos = (ano - 2, ano - 1, ano)
        return cls(base=(os.environ.get("SIGPMF_SRC_AUDESP_BASE") or BASE_PADRAO).rstrip("/"),
                   municipio=(os.environ.get("SIGPMF_SRC_AUDESP_MUNICIPIO") or "franca").strip().lower(), anos=anos)

    def url(self, tipo: str, ano: int) -> str:
        u = f"{self.base}/{CAMINHOS[tipo].format(m=self.municipio, ano=ano)}"
        confere_host(u)
        return u


def confere_host(url: str) -> None:
    p = urlparse(url)
    if p.scheme != "https" or p.hostname != HOST_PERMITIDO:
        raise PermissionError("Conector AUDESP: só o portal de transparência do TCE-SP é permitido.")


@dataclass(frozen=True)
class Assinatura:
    last_modified: str | None
    tamanho: int | None

    def texto(self) -> str:
        return f"{self.last_modified or '?'}|{self.tamanho if self.tamanho is not None else '?'}"


def _com_tentativas(fazer, tentativas: int = TENTATIVAS, pausa_s: float = 2.0):
    ultimo: Exception | None = None
    for i in range(tentativas):
        try:
            return fazer()
        except (httpx.TransportError, httpx.HTTPStatusError) as exc:
            ultimo = exc
            if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code < 500:
                break  # 4xx não melhora repetindo
            if i + 1 < tentativas:
                time.sleep(pausa_s * (i + 1))
    if isinstance(ultimo, httpx.HTTPStatusError):
        raise ErroDownload(f"O portal do TCE respondeu HTTP {ultimo.response.status_code}.")
    raise ErroDownload(f"Sem resposta do portal do TCE ({type(ultimo).__name__}).")


def assinatura(cliente: httpx.Client, url: str) -> Assinatura | None:
    """HEAD: assinatura do arquivo; None quando o TCE ainda não publicou (404)."""
    confere_host(url)

    def head():
        r = cliente.head(url)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        n = r.headers.get("content-length")
        return Assinatura(r.headers.get("last-modified"), int(n) if n and n.isdigit() else None)
    return _com_tentativas(head)


def baixa(cliente: httpx.Client, url: str) -> bytes:
    confere_host(url)

    def get():
        with cliente.stream("GET", url) as r:
            r.raise_for_status()
            partes, total = [], 0
            for parte in r.iter_bytes():
                total += len(parte)
                if total > MAX_ZIP:
                    raise ArquivoInvalido("Arquivo do TCE maior que o limite de 100 MB.")
                partes.append(parte)
            return b"".join(partes)
    return _com_tentativas(get)


def cliente_http() -> httpx.Client:
    return httpx.Client(timeout=httpx.Timeout(60.0, connect=15.0), follow_redirects=False,
                        headers={"User-Agent": "SIGPMF/AUDESP (somente leitura)"})


def sha256(conteudo: bytes) -> str:
    """SHA-256 do CSV (de dentro do zip, quando for zip): recompactar o mesmo CSV muda o zip, mas não o conteúdo."""
    h = hashlib.sha256()
    if conteudo[:2] == b"PK":
        bruto = abre_csv(conteudo).buffer
        while parte := bruto.read(1 << 20):
            h.update(parte)
    else:
        h.update(conteudo)
    return h.hexdigest()


def abre_csv(conteudo: bytes) -> io.TextIOBase:
    """Zip (um .csv dentro, com tetos) ou o próprio CSV (upload do plano B)."""
    if conteudo[:2] == b"PK":
        try:
            zf = zipfile.ZipFile(io.BytesIO(conteudo))
        except zipfile.BadZipFile as exc:
            raise ArquivoInvalido("O zip do TCE está corrompido.") from exc
        membros = [i for i in zf.infolist() if not i.is_dir()]
        if len(membros) != 1 or not membros[0].filename.lower().endswith(".csv"):
            raise ArquivoInvalido("O zip do TCE deveria ter exatamente um arquivo .csv.")
        if membros[0].file_size > MAX_CSV:
            raise ArquivoInvalido("O CSV dentro do zip passa do limite de 500 MB.")
        return io.TextIOWrapper(_Limitado(zf.open(membros[0]), MAX_CSV), encoding=ENCODING, newline="")
    if len(conteudo) > MAX_CSV:
        raise ArquivoInvalido("O CSV passa do limite de 500 MB.")
    return io.TextIOWrapper(io.BytesIO(conteudo), encoding=ENCODING, newline="")


class _Limitado(io.RawIOBase):
    """Protege contra zip que mente o tamanho: para de ler no teto, qualquer que seja o cabeçalho do zip."""

    def __init__(self, base, limite: int):
        self._b, self._resta = base, limite

    def readable(self) -> bool:
        return True

    def readinto(self, buf) -> int:
        n = self._b.readinto(buf)
        self._resta -= n
        if self._resta < 0:
            raise ArquivoInvalido("O CSV dentro do zip passa do limite de 500 MB.")
        return n


def tipo_do_cabecalho(cabecalho: list[str]) -> str:
    cab = tuple(c.strip().lstrip("﻿") for c in cabecalho)
    for tipo, cols in COLUNAS.items():
        if cab == cols:
            return tipo
    melhor = max(COLUNAS, key=lambda t: len(set(cab) & set(COLUNAS[t])))
    falta = [c for c in COLUNAS[melhor] if c not in cab]
    if falta:
        raise ArquivoInvalido(f"Este arquivo não parece ser do AUDESP ({melhor}): coluna {falta[0]!r} ausente.")
    raise ArquivoInvalido(f"Este arquivo não parece ser do AUDESP ({melhor}): colunas fora da ordem ou a mais.")


def linhas(texto: io.TextIOBase) -> tuple[str, Iterator[dict]]:
    """(tipo, gerador de linhas já prontas para a bronze): strings sem espaço nas pontas, sem as colunas de fora."""
    leitor = csv.reader(texto, delimiter=";")
    try:
        cabecalho = next(leitor)
    except StopIteration as exc:
        raise ArquivoInvalido("Arquivo vazio.") from exc
    tipo = tipo_do_cabecalho(cabecalho)
    cols, fora = COLUNAS[tipo], set(FORA_DA_BRONZE.get(tipo, ()))

    def gera():
        for n, valores in enumerate(leitor, start=2):
            if not valores or all(not v.strip() for v in valores):
                continue
            if len(valores) != len(cols):
                raise ArquivoInvalido(f"Linha {n} com {len(valores)} colunas (esperado {len(cols)}).")
            r = {c: v.strip() for c, v in zip(cols, valores, strict=True) if c not in fora}
            if tipo == "restos_a_pagar":
                r["_id"] = hashlib.sha256("\x1f".join(valores).encode()).hexdigest()[:40]
                if r["ID Credor"].upper().startswith("CPF"):
                    r["ID Credor"] = "CPF"
            yield r
    return tipo, gera()


def ano_da_linha(tipo: str, r: dict) -> int | None:
    v = r.get("Ano Balancete" if tipo == "restos_a_pagar" else "ano_exercicio") or ""
    return int(v) if v.isdigit() else None
