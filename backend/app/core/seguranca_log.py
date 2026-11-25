"""Mascaramento de DSN/senha em logs, respostas e saída de terminal.

Erros de conexão do psycopg/SQLAlchemy podem carregar a string de conexão (``postgresql://user:senha@host``) ou
``password=...`` em formato chave=valor. ``mascarar`` troca a senha por ``***``; ``FiltroSegredos`` aplica isso a todo
registro de log (mensagem, args e traceback) e ``instalar_filtro`` o pendura nos handlers do raiz, do uvicorn e do app.
Respostas da API e CLIs mostram só a classe do erro (``mensagem_erro_banco``), nunca ``str(exc)``.
"""
from __future__ import annotations

import logging
import re

MASCARA = "***"

# esquema://usuario:senha@  (senha sem @ / espaço; cobre postgresql+psycopg://, mssql+pyodbc:// etc.)
_URL = re.compile(r"(?P<pre>[a-zA-Z][a-zA-Z0-9+.\-]*://[^:/@\s]*:)(?P<senha>[^@\s]*)(?P<pos>@)")
# password=..., pwd=..., PGPASSWORD=... (aspas simples/duplas ou valor até espaço/;/&)
_KV = re.compile(
    r"(?P<pre>\b(?:password|passwd|pwd|pgpassword|senha)\s*[=:]\s*)"
    r"(?P<senha>'[^']*'|\"[^\"]*\"|[^\s;&,'\")]+)",
    re.IGNORECASE,
)


def mascarar(texto: object) -> str:
    """Devolve o texto com toda senha de URL e de chave=valor trocada por ***."""
    s = texto if isinstance(texto, str) else str(texto)
    s = _URL.sub(lambda m: f"{m['pre']}{MASCARA}{m['pos']}", s)
    return _KV.sub(lambda m: f"{m['pre']}{MASCARA}", s)


def mensagem_erro_banco(exc: BaseException) -> str:
    """Texto seguro para API/CLI: só a classe do erro (nunca str(exc))."""
    return f"Falha de acesso ao banco ({type(exc).__name__})."


# Access log do uvicorn: logger.info('%s - "%s %s HTTP/%s" %d', client_addr, method, full_path,
# http_version, status_code). O AccessFormatter desempacota record.args. Renderizar a mensagem e
# zerar args (o caminho dos outros logs) gera TypeError: cannot unpack non-iterable NoneType object
# e a linha de acesso some. Aqui a tupla fica, com senha/DSN mascarados dentro das strings.
def _args_access_uvicorn(record: logging.LogRecord) -> tuple | None:
    if record.name != "uvicorn.access":
        return None
    args = record.args
    if not isinstance(args, tuple) or len(args) != 5:
        return None
    client, method, path, version, status = args
    if not all(isinstance(p, str) for p in (client, method, path, version)):
        return None
    if isinstance(status, bool) or not isinstance(status, int):
        return None
    return args


def _mascarar_args_access(args: tuple) -> tuple:
    client, method, path, version, status = args
    return (mascarar(client), mascarar(method), mascarar(path), mascarar(version), status)


class FiltroSegredos(logging.Filter):
    """Mascara senha/DSN na mensagem, nos args e no traceback do registro. Nunca descarta o registro.

    No access log do uvicorn a tupla de 5 args é preservada (o formatter do uvicorn a desempacota);
    senha e DSN que estejam no caminho ou na query continuam mascarados.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        args_access = _args_access_uvicorn(record)
        if args_access is not None:
            record.args = _mascarar_args_access(args_access)
        else:
            try:
                msg = record.getMessage()
            except Exception:  # noqa: BLE001  (formatação quebrada: segue com o msg cru)
                msg = str(record.msg)
            record.msg, record.args = mascarar(msg), None
        if record.exc_info and not record.exc_text:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        if record.exc_text:
            record.exc_text = mascarar(record.exc_text)
        if record.stack_info:
            record.stack_info = mascarar(record.stack_info)
        return True


LOGGERS = ("", "uvicorn", "uvicorn.error", "uvicorn.access", "sigpmf", "sqlalchemy", "alembic")


def instalar_filtro(*extras: str) -> FiltroSegredos:
    """Pendura um único FiltroSegredos nos loggers e em todos os handlers existentes (idempotente)."""
    filtro = _FILTRO
    nomes = set(LOGGERS) | set(extras) | set(logging.root.manager.loggerDict)
    for nome in nomes:
        lg = logging.getLogger(nome)
        if not isinstance(lg, logging.Logger):
            continue
        if filtro not in lg.filters:
            lg.addFilter(filtro)
        for h in lg.handlers:
            if filtro not in h.filters:
                h.addFilter(filtro)
    return filtro


_FILTRO = FiltroSegredos()
