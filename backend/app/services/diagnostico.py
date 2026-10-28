"""Diagnóstico seguro de falhas da origem (SQL Server) e normalização de tipos do driver.

Nada aqui guarda valores de linha, host, usuário ou senha: só a classe da exceção, o número do erro do
SQL Server, a tabela sendo lida, o nome do objeto inexistente (207/208) e o último ponto do traceback
(arquivo:linha em função).
"""
import datetime as dt
import hashlib
import os
import re
import traceback
import uuid
from decimal import Decimal

ORIGEM_VAZIA = "origem_vazia"
MSG_ORIGEM_VAZIA = "Nenhuma linha lida da origem. Verifique a conexão ou o banco configurado."
_NOME = re.compile(r"Invalid (?:column|object) name '([A-Za-z0-9_.\[\]]{1,128})'")


_VERSAO = re.compile(r"rowversion|timestamp|versao", re.IGNORECASE)


def valor_json(v, coluna: str | None = None):
    """Tipos do pymssql -> JSON (JSONB da bronze): datetime/date/time -> ISO; Decimal -> int ou texto;
    bytes -> hex '0x..'; UUID -> texto."""
    if isinstance(v, (dt.datetime, dt.date, dt.time)):
        return v.isoformat()
    if isinstance(v, Decimal):
        return int(v) if v == v.to_integral_value() else str(v)
    if isinstance(v, (bytes, bytearray, memoryview)):
        return bytes_json(bytes(v), coluna)
    if isinstance(v, uuid.UUID):
        return str(v)
    return v


BINARIO_MAX = 1024  # bytes > 1 KB na bronze: só tamanho + sha256 (nota do Tester)


def bytes_json(b: bytes, coluna: str | None = None):
    """varchar que o driver devolve como bytes vira texto (utf-8, senão cp1252); binário curto vira '0x..';
    binário grande vira {bytes, sha256}."""
    if len(b) > BINARIO_MAX:
        return {"bytes": len(b), "sha256": hashlib.sha256(b).hexdigest()}
    if len(b) == 8 or (coluna and _VERSAO.search(coluna)):  # rowversion/timestamp: sempre 0x hex (formato estável)
        return "0x" + b.hex()
    for enc in ("utf-8", "cp1252"):
        try:
            t = b.decode(enc)
        except UnicodeDecodeError:
            continue
        if t and all(ch.isprintable() or ch in "\t\r\n" for ch in t):
            return t
    return "0x" + b.hex()


def linha_json(r, colunas=None) -> dict:
    """Aceita dict (as_dict=True) ou tupla (as_dict=False, na ordem das colunas da política)."""
    if not isinstance(r, dict):
        if colunas is None or len(colunas) != len(r):
            raise ValueError("Linha da origem em tupla sem a lista de colunas correspondente.")
        r = dict(zip(colunas, r, strict=True))
    return {k: valor_json(v, k) for k, v in r.items()}


class FalhaLeitura(Exception):
    """Envolve a exceção original com a tabela que estava sendo lida/gravada."""

    def __init__(self, tabela: str | None, original: BaseException):
        super().__init__(type(original).__name__)
        self.tabela, self.original = tabela, original


def _texto(exc: BaseException) -> str:
    partes = []
    for a in getattr(exc, "args", ()):
        partes.append(a.decode("utf-8", "replace") if isinstance(a, (bytes, bytearray)) else str(a))
    return " ".join(partes)


def categoria(exc: BaseException) -> dict:
    """{classe, numero, tabela, objeto, local} — seguro para gravar e mostrar."""
    tabela = None
    if isinstance(exc, FalhaLeitura):
        tabela, exc = exc.tabela, exc.original
    args = getattr(exc, "args", ())
    numero = args[0] if args and isinstance(args[0], int) and not isinstance(args[0], bool) else None
    objeto = None
    if numero in (207, 208):
        m = _NOME.search(_texto(exc))
        objeto = m.group(1) if m else None
    tb = traceback.extract_tb(exc.__traceback__) if exc.__traceback__ else []
    local = None
    if tb:
        f = tb[-1]
        local = f"{os.path.basename(f.filename)}:{f.lineno} in {f.name}"[:200]
    return {"classe": type(exc).__name__[:80], "numero": numero, "tabela": tabela, "objeto": objeto, "local": local}


def motivo(classe: str | None, numero: int | None, tabela: str | None, objeto: str | None) -> str:
    """Texto curto em pt-BR para o card."""
    em = f" (tabela {tabela})" if tabela else ""
    if numero == 207:
        return f"Coluna inexistente na origem: {objeto or '?'}{em}. Ajustar a lista de colunas da política."
    if numero == 208:
        return f"Tabela ou view inexistente na origem: {objeto or tabela or '?'}. Conferir banco e schema (dbo)."
    if numero in (229, 230, 262, 297):
        return f"Sem permissão de leitura na origem{em}."
    if numero == 4060:
        return "Banco configurado não existe ou o usuário não tem acesso a ele."
    if numero == 18456:
        return "Login recusado pela origem (usuário ou senha)."
    if numero in (20009, 20003, 20002) or (classe or "").lower().endswith("timeouterror"):
        return "Servidor de origem não respondeu (rede, porta ou timeout)."
    if classe in ("MSSQLDriverException", "InterfaceError"):
        return f"A conexão com a origem caiu durante a consulta{em} (provável tempo limite; ver SQLSERVER_CONSULTA_TIMEOUT)."
    if classe == "TypeError":
        return f"Erro interno ao converter os dados lidos{em}."
    if classe == "ProgrammingError":
        return f"Consulta recusada pelo SQL Server{em}" + (f" (erro {numero})." if numero else ".")
    if classe == "OperationalError":
        return "Falha de conexão com a origem" + (f" (erro {numero})." if numero else ".")
    return f"Erro na origem ({classe}){em}."
