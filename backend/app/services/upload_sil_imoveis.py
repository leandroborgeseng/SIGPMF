"""Recebe o CSV da SIL por HTTP e grava em disco em pedaços, sem montar o arquivo na memória.

O parser multipart lê o corpo aos poucos e para ao passar do teto. A carga em si é
`carga_sil_imoveis.carregar` (a mesma do comando), disparada depois, em background.
"""
from __future__ import annotations

import os
import re
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path
from urllib.parse import unquote

from starlette.requests import Request

LIMITE_BYTES = 200 * 1024 * 1024
FOLGA_BYTES = 1024 * 1024  # envelope do multipart em volta do arquivo
DIRETORIO = Path(tempfile.gettempdir())
_TETO_PREAMBULO = 64 * 1024
_TETO_CABECALHO = 16 * 1024


class ExcedeuLimite(Exception):
    """Corpo ou arquivo acima do teto. A rota responde 413."""


class EnvioInvalido(Exception):
    """Multipart fora do contrato. A mensagem é fixa: não leva conteúdo do arquivo."""


def mensagem_grande() -> str:
    mb = LIMITE_BYTES // (1024 * 1024)
    if mb >= 1:
        return f"Arquivo grande demais (limite de {mb} MB)."
    return "Arquivo grande demais."


def novo_temporario() -> Path:
    DIRETORIO.mkdir(parents=True, exist_ok=True)
    fd, nome = tempfile.mkstemp(prefix="sigpmf-sil-", suffix=".csv", dir=DIRETORIO)
    os.close(fd)
    return Path(nome)


def _boundary(content_type: str) -> bytes:
    if "multipart/form-data" not in content_type.lower():
        raise EnvioInvalido("Envie o arquivo como multipart/form-data.")
    m = re.search(r"""boundary\s*=\s*(?:"([^"]+)"|'([^']+)'|([^;\s]+))""", content_type, re.I)
    bruto = next((g for g in (m.groups() if m else ()) if g), None)
    if not bruto or len(bruto) > 200:
        raise EnvioInvalido("Envie o arquivo como multipart/form-data.")
    try:
        return bruto.encode("ascii")
    except UnicodeEncodeError:
        raise EnvioInvalido("Envie o arquivo como multipart/form-data.") from None


def _e_csv(nome: str) -> bool:
    base = Path(nome.replace("\\", "/")).name.strip()
    return base.lower().endswith(".csv") and Path(base).suffix.lower() == ".csv"


def _disposicao(valor: str) -> tuple[str, str | None]:
    arquivo = None
    estrela = re.search(r"filename\*\s*=\s*(?:UTF-8|utf-8)''([^;]+)", valor)
    if estrela:
        arquivo = unquote(estrela.group(1).strip().strip('"'))
    else:
        citado = re.search(r'filename\s*=\s*"((?:\\.|[^"\\])*)"', valor)
        if citado:
            arquivo = citado.group(1).replace('\\"', '"').replace("\\\\", "\\")
        else:
            simples = re.search(r"filename\s*=\s*([^;]+)", valor)
            if simples:
                arquivo = simples.group(1).strip().strip('"')
    campo = ""
    nome = re.search(r'name\s*=\s*"([^"]*)"', valor) or re.search(r"name\s*=\s*([^;]+)", valor)
    if nome:
        campo = nome.group(1).strip().strip('"')
    if arquivo is not None:
        arquivo = arquivo.strip()
    return campo, arquivo


def _cabecalho(bloco: str, nome: str) -> str:
    for linha in bloco.split("\r\n"):
        if ":" not in linha:
            continue
        chave, valor = linha.split(":", 1)
        if chave.strip().lower() == nome:
            return valor.strip()
    return ""


class _Descarte:
    def write(self, pedaco: bytes) -> int:
        return len(pedaco)


class _Buffer:
    def __init__(self, chunks: AsyncIterator[bytes], teto: int):
        self.chunks = chunks
        self.teto = teto
        self.buf = bytearray()
        self.lidos = 0
        self.fim = False

    async def _enche(self) -> bool:
        if self.fim:
            return False
        while True:
            try:
                pedaco = await anext(self.chunks)
            except StopAsyncIteration:
                self.fim = True
                return False
            if not pedaco:
                continue
            self.lidos += len(pedaco)
            if self.lidos > self.teto:
                raise ExcedeuLimite()
            self.buf += bytes(pedaco)
            return True

    async def achar(self, marca: bytes, teto_buf: int) -> bytes:
        while True:
            i = self.buf.find(marca)
            if i >= 0:
                if i > teto_buf:
                    raise EnvioInvalido("Envio multipart inválido.")
                antes = bytes(self.buf[:i])
                del self.buf[: i + len(marca)]
                return antes
            if len(self.buf) > teto_buf:
                raise EnvioInvalido("Envio multipart inválido.")
            if not await self._enche():
                raise EnvioInvalido("Envio multipart incompleto.")

    async def garantir(self, n: int) -> None:
        while len(self.buf) < n:
            if not await self._enche():
                return

    async def copiar_ate(self, marca: bytes, out, limite_arquivo: int) -> int:
        """Grava até a marca, guardando só o fim que pode ser um pedaço dela."""
        total = 0
        guarda = max(len(marca) - 1, 0)
        while True:
            i = self.buf.find(marca)
            if i >= 0:
                pedaco = bytes(self.buf[:i])
                total += len(pedaco)
                if total > limite_arquivo:
                    raise ExcedeuLimite()
                if pedaco:
                    out.write(pedaco)
                del self.buf[: i + len(marca)]
                return total
            if len(self.buf) > guarda:
                soltar = len(self.buf) - guarda
                pedaco = bytes(self.buf[:soltar])
                total += len(pedaco)
                if total > limite_arquivo:
                    raise ExcedeuLimite()
                out.write(pedaco)
                del self.buf[:soltar]
            if not await self._enche():
                raise EnvioInvalido("Envio multipart incompleto.")

    async def descartar_resto(self) -> None:
        self.buf.clear()
        while await self._enche():
            self.buf.clear()


async def _gravar_partes(buf: _Buffer, boundary: bytes, destino: Path, limite: int) -> str:
    marca = b"--" + boundary
    fecha = b"\r\n" + marca
    await buf.achar(marca, _TETO_PREAMBULO)
    nome_arquivo: str | None = None
    while True:
        await buf.garantir(2)
        inicio = bytes(buf.buf[:2])
        if inicio == b"--" or buf.fim and not buf.buf:
            break
        if inicio != b"\r\n":
            raise EnvioInvalido("Envio multipart inválido.")
        del buf.buf[:2]
        bloco = (await buf.achar(b"\r\n\r\n", _TETO_CABECALHO)).decode("latin-1", errors="replace")
        campo, arquivo = _disposicao(_cabecalho(bloco, "content-disposition"))
        if arquivo is None:
            await buf.copiar_ate(fecha, _Descarte(), limite)
            continue
        if campo != "arquivo" or not _e_csv(arquivo):
            raise EnvioInvalido(
                "Envie um arquivo .csv." if campo == "arquivo" else "Envie o arquivo CSV no campo arquivo.")
        with destino.open("wb") as out:
            tamanho = await buf.copiar_ate(fecha, out, limite)
        if tamanho <= 0:
            raise EnvioInvalido("Arquivo vazio.")
        nome_arquivo = arquivo
        break
    if nome_arquivo is None:
        raise EnvioInvalido("Envie o arquivo CSV no campo arquivo.")
    await buf.descartar_resto()
    return nome_arquivo


async def salvar_de_stream(chunks: AsyncIterator[bytes], content_type: str, destino: Path, *,
                           limite: int | None = None, folga: int | None = None,
                           content_length: int | None = None) -> str:
    """Grava o campo `arquivo` em `destino`. Não acumula o corpo: só o pedaço corrente e a cauda da marca."""
    limite = LIMITE_BYTES if limite is None else limite
    folga = FOLGA_BYTES if folga is None else folga
    if content_length is not None and content_length > limite + folga:
        raise ExcedeuLimite()
    boundary = _boundary(content_type)
    buf = _Buffer(chunks, limite + folga)
    return await _gravar_partes(buf, boundary, destino, limite)


async def salvar_request(request: Request, destino: Path) -> str:
    cl = request.headers.get("content-length")
    tamanho = int(cl) if cl and cl.isdigit() else None
    return await salvar_de_stream(
        request.stream(), request.headers.get("content-type", ""), destino, content_length=tamanho)
