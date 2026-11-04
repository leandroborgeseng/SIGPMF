"""Dados abertos da ANS e população do IBGE: lista, download e leitura. SOMENTE LEITURA; sem login e sem pessoa.

- ANS: "Informações consolidadas de beneficiários" (PDA 024), um zip por UF e mês, com beneficiários por município,
  operadora, faixa etária e tipo de contrato. A taxa pronta da ANS (PDA 047) não serve: em 2026 traz a população zerada.
- IBGE: estimativa anual de população (agregado 6579) e Censo 2022 por idade (agregado 9514).
- Só os hosts da allowlist, timeout, 3 tentativas. O zip vai para um arquivo temporário e é lido em streaming, com teto.
"""
import csv
import io
import re
import time
import zipfile
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import urlparse

import httpx

HOSTS = frozenset({"dadosabertos.ans.gov.br", "servicodados.ibge.gov.br"})
BASE_ANS = "https://dadosabertos.ans.gov.br/FTP/PDA/informacoes_consolidadas_de_beneficiarios-024"
BASE_IBGE = "https://servicodados.ibge.gov.br/api/v3/agregados"
MAX_ZIP = 600 * 1024 * 1024
MAX_CSV = 4 * 1024 * 1024 * 1024
TENTATIVAS = 3
IBGE_FRANCA = "3516200"
UF_SP = "35"
COLUNAS = ("CD_MUNICIPIO", "NM_MUNICIPIO", "NM_RAZAO_SOCIAL", "DE_FAIXA_ETARIA", "DE_CONTRATACAO_PLANO", "COBERTURA_ASSIST_PLAN",
           "QT_BENEFICIARIO_ATIVO", "QT_BENEFICIARIO_ADERIDO", "QT_BENEFICIARIO_CANCELADO")
# Censo 2022, tabela 9514: categoria de idade -> faixa usada nos painéis
IDADE_CENSO = {93070: "0 a 4 anos", 93084: "5 a 9 anos", 93085: "10 a 14 anos", 93086: "15 a 19 anos", 93087: "20 a 24 anos", 93088: "25 a 29 anos",
               93089: "30 a 34 anos", 93090: "35 a 39 anos", 93091: "40 a 44 anos", 93092: "45 a 49 anos", 93093: "50 a 54 anos", 93094: "55 a 59 anos",
               93095: "60 a 64 anos", 93096: "65 a 69 anos", 93097: "70 a 74 anos", 93098: "75 a 79 anos", 49108: "80 anos ou mais", 49109: "80 anos ou mais",
               60040: "80 anos ou mais", 60041: "80 anos ou mais", 6653: "80 anos ou mais"}
FAIXAS = tuple(dict.fromkeys(IDADE_CENSO.values()))
_JUNTA_FAIXA = {"Menos de 1 ano": "0 a 4 anos", "1 a 4 anos": "0 a 4 anos", "15 a 17 anos": "15 a 19 anos", "18 a 19 anos": "15 a 19 anos"}


class ArquivoAnsInvalido(ValueError):
    """Arquivo fora do formato esperado. A mensagem não traz conteúdo do arquivo."""


def faixa_padrao(texto: str) -> str | None:
    """Faixa da ANS -> faixa do Censo (a ANS separa menos de 1 ano e 15-17/18-19). Desconhecida não entra por idade."""
    f = _JUNTA_FAIXA.get(texto, texto)
    return f if f in FAIXAS else None


def _confere_host(url: str) -> None:
    if urlparse(url).hostname not in HOSTS:
        raise ArquivoAnsInvalido("Host fora da lista permitida.")


def _com_tentativas(fn):
    for t in range(TENTATIVAS):
        try:
            return fn()
        except httpx.HTTPError:
            if t == TENTATIVAS - 1:
                raise
            time.sleep(2 ** t)
    return None


def lista_meses(cliente: httpx.Client) -> list[tuple[int, int]]:
    """Meses publicados, do mais antigo ao mais novo, pela listagem do diretório."""
    r = _com_tentativas(lambda: cliente.get(f"{BASE_ANS}/"))
    r.raise_for_status()
    return sorted({(int(a), int(m)) for a, m in re.findall(r'href="(\d{4})(\d{2})/"', r.text)})


def url_mes(ano: int, mes: int) -> str:
    return f"{BASE_ANS}/{ano}{mes:02d}/pda-024-icb-SP-{ano}_{mes:02d}.zip"


def baixa_mes(cliente: httpx.Client, ano: int, mes: int, destino: Path) -> None:
    url = url_mes(ano, mes)
    _confere_host(url)

    def baixa():
        total = 0
        with cliente.stream("GET", url) as r, destino.open("wb") as f:
            r.raise_for_status()
            for bloco in r.iter_bytes(1024 * 1024):
                total += len(bloco)
                if total > MAX_ZIP:
                    raise ArquivoAnsInvalido("Arquivo da ANS maior que o teto.")
                f.write(bloco)
    _com_tentativas(baixa)


def linhas_do_zip(caminho: Path) -> Iterator[dict]:
    """Linhas do CSV do zip, em streaming. Confere o cabeçalho antes de entregar a primeira."""
    with zipfile.ZipFile(caminho) as z:
        csvs = [i for i in z.infolist() if i.filename.lower().endswith(".csv")]
        if len(csvs) != 1:
            raise ArquivoAnsInvalido("O zip da ANS deveria ter um único CSV.")
        if csvs[0].file_size > MAX_CSV:
            raise ArquivoAnsInvalido("CSV da ANS maior que o teto.")
        with z.open(csvs[0]) as bruto:
            leitor = csv.reader(io.TextIOWrapper(bruto, encoding="utf-8-sig", errors="replace"), delimiter=";")
            cab = [c.strip().upper() for c in next(leitor, [])]
            faltam = [c for c in COLUNAS if c not in cab]
            if faltam:
                raise ArquivoAnsInvalido(f"Colunas ausentes no arquivo da ANS: {', '.join(faltam)}.")
            pos = {c: cab.index(c) for c in COLUNAS}
            for v in leitor:
                if len(v) == len(cab):
                    yield {c: v[i] for c, i in pos.items()}


def populacao_estimada(cliente: httpx.Client, anos: list[int]) -> list[dict]:
    """População estimada dos municípios de SP. Código com 7 dígitos no IBGE; a ANS usa 6 (sem o verificador)."""
    url = f"{BASE_IBGE}/6579/periodos/{'|'.join(map(str, anos))}/variaveis/9324"
    _confere_host(url)
    r = _com_tentativas(lambda: cliente.get(url, params={"localidades": f"N6[N3[{UF_SP}]]"}))
    r.raise_for_status()
    out = []
    for s in r.json()[0]["resultados"][0]["series"]:
        for ano, v in s["serie"].items():
            if str(v).isdigit():
                out.append({"cd_municipio": int(s["localidade"]["id"][:6]), "ano": int(ano), "populacao": int(v)})
    return out


def censo_por_faixa(cliente: httpx.Client) -> list[dict]:
    """Censo 2022 de Franca por faixa de 5 anos (80 ou mais juntos)."""
    url = f"{BASE_IBGE}/9514/periodos/2022/variaveis/93"
    _confere_host(url)
    cats = ",".join(map(str, IDADE_CENSO))
    r = _com_tentativas(lambda: cliente.get(url, params={"localidades": f"N6[{IBGE_FRANCA}]", "classificacao": f"287[{cats}]|2[6794]|286[113635]"}))
    r.raise_for_status()
    soma: dict[str, int] = {}
    for res in r.json()[0]["resultados"]:
        cat = next(c for c in res["classificacoes"] if c["id"] == "287")
        cod = int(next(iter(cat["categoria"])))
        v = res["series"][0]["serie"].get("2022")
        if str(v).isdigit():
            soma[IDADE_CENSO[cod]] = soma.get(IDADE_CENSO[cod], 0) + int(v)
    if set(soma) != set(FAIXAS):
        raise ValueError("Censo do IBGE sem todas as faixas de idade.")
    return [{"cd_municipio": int(IBGE_FRANCA[:6]), "faixa": f, "populacao": n} for f, n in soma.items()]
