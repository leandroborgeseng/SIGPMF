"""Mascaramento de DSN/senha e de CPF/CNPJ em logs, respostas e saída de terminal.

Erros de conexão do psycopg/SQLAlchemy podem carregar a string de conexão (``postgresql://user:senha@host``) ou
``password=...`` em formato chave=valor. ``mascarar`` troca a senha por ``***`` e CPF/CNPJ por um token fixo;
``FiltroSegredos`` aplica isso a todo registro de log (mensagem, args e traceback) e ``instalar_filtro`` o pendura
nos handlers do raiz, do uvicorn e do app. Respostas da API e CLIs mostram só a classe do erro
(``mensagem_erro_banco``), nunca ``str(exc)``.
"""
from __future__ import annotations

import logging
import re

from app.identity.cpf import valido as cpf_valido
from app.identity.documento import cnpj_valido

MASCARA = "***"
# Token fixo, sem dígito. A máscara de tela (***.456.789-** e **.456.***/****-**) guarda parte do número
# de propósito para o operador; no log isso ainda identifica a pessoa. Aqui o log não conserva dígito.
MASCARA_CPF = "***CPF***"
MASCARA_CNPJ = "***CNPJ***"

# esquema://usuario:senha@  (senha sem @ / espaço; cobre postgresql+psycopg://, mssql+pyodbc:// etc.)
_URL = re.compile(r"(?P<pre>[a-zA-Z][a-zA-Z0-9+.\-]*://[^:/@\s]*:)(?P<senha>[^@\s]*)(?P<pos>@)")
# password=..., pwd=..., PGPASSWORD=... (aspas simples/duplas ou valor até espaço/;/&)
_KV = re.compile(
    r"(?P<pre>\b(?:password|passwd|pwd|pgpassword|senha)\s*[=:]\s*)"
    r"(?P<senha>'[^']*'|\"[^\"]*\"|[^\s;&,'\")]+)",
    re.IGNORECASE,
)

# CPF/CNPJ no access log (caminho e query) e nas demais mensagens que passam por mascarar.
# Não decodificamos a URL inteira: %20 e o resto do texto ficam. Só . - / contam também como %2E %2D %2F.
# Não mascaramos qualquer número longo: inscrição, telefone e id de outro tamanho permanecem.
#   1. Forma pontuada, em qualquer lugar: 000.000.000-00 e 00.000.000/0000-00 (literal ou URL-encoded).
#   2. Valor inteiro de cpf=, cnpj= ou documento= com 11 ou 14 dígitos (com ou sem essa pontuação).
#   3. Segmento inteiro do path com 11 dígitos (***CPF***) ou 14 (***CNPJ***), mesmo com DV inválido:
#      é assim que a API endereça a pessoa (/cidadaos/52998224725/genealogia).
#   4. Fora desses lugares, 11 ou 14 dígitos soltos só caem se o DV for válido. Um telefone ou um id
#      de 11 dígitos com DV inválido, em outro parâmetro, fica. CNPJ alfanumérico (CN1) não entra:
#      o padrão [0-9A-Z]{14} comeria token que não é documento.
_PONTO = r"(?:\.|%2[Ee])"
_HIFEN = r"(?:-|%2[Dd])"
_BARRA = r"(?:/|%2[Ff])"
_CPF_PONTUADO = re.compile(rf"(?<!\d)\d{{3}}{_PONTO}\d{{3}}{_PONTO}\d{{3}}{_HIFEN}\d{{2}}(?!\d)")
_CNPJ_PONTUADO = re.compile(rf"(?<!\d)\d{{2}}{_PONTO}\d{{3}}{_PONTO}\d{{3}}{_BARRA}\d{{4}}{_HIFEN}\d{{2}}(?!\d)")
_PARAM_DOC = re.compile(r"(?i)(?<![A-Za-z0-9_])(?P<nome>cpf|cnpj|documento)=(?P<valor>[^&#\s]*)")
_SEGMENTO = re.compile(r"(?<=/)(?P<doc>\d{14}|\d{11})(?=/|[?#]|$)")
_SOLTO = re.compile(r"(?<!\d)(?P<doc>\d{14}|\d{11})(?!\d)")
_CPF_PONTUADO_DECODED = re.compile(r"\d{3}\.\d{3}\.\d{3}-\d{2}")
_CNPJ_PONTUADO_DECODED = re.compile(r"\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}")


def _decodifica_separador(valor: str) -> str:
    s = re.sub(r"%2E", ".", valor, flags=re.IGNORECASE)
    s = re.sub(r"%2D", "-", s, flags=re.IGNORECASE)
    return re.sub(r"%2F", "/", s, flags=re.IGNORECASE)


def _rotulo_valor_doc(valor: str) -> str | None:
    """***CPF*** ou ***CNPJ*** se o valor inteiro é um documento; senão None (não mexe no resto do parâmetro)."""
    dec = _decodifica_separador(valor)
    if _CPF_PONTUADO_DECODED.fullmatch(dec) or re.fullmatch(r"\d{11}", dec):
        return MASCARA_CPF
    if _CNPJ_PONTUADO_DECODED.fullmatch(dec) or re.fullmatch(r"\d{14}", dec):
        return MASCARA_CNPJ
    return None


def _sub_param(m: re.Match[str]) -> str:
    rotulo = _rotulo_valor_doc(m.group("valor"))
    if rotulo is None:
        return m.group(0)
    return f"{m.group('nome')}={rotulo}"


def _sub_segmento(m: re.Match[str]) -> str:
    return MASCARA_CNPJ if len(m.group("doc")) == 14 else MASCARA_CPF


def _sub_solto(m: re.Match[str]) -> str:
    doc = m.group("doc")
    if len(doc) == 14 and cnpj_valido(doc):
        return MASCARA_CNPJ
    if len(doc) == 11 and cpf_valido(doc):
        return MASCARA_CPF
    return doc


def _mascarar_documentos(s: str) -> str:
    s = _CNPJ_PONTUADO.sub(MASCARA_CNPJ, s)
    s = _CPF_PONTUADO.sub(MASCARA_CPF, s)
    s = _PARAM_DOC.sub(_sub_param, s)
    s = _SEGMENTO.sub(_sub_segmento, s)
    return _SOLTO.sub(_sub_solto, s)


def mascarar(texto: object) -> str:
    """Devolve o texto com senha de URL, senha chave=valor e CPF/CNPJ trocados pela máscara fixa."""
    s = texto if isinstance(texto, str) else str(texto)
    s = _URL.sub(lambda m: f"{m['pre']}{MASCARA}{m['pos']}", s)
    s = _KV.sub(lambda m: f"{m['pre']}{MASCARA}", s)
    return _mascarar_documentos(s)


def mensagem_erro_banco(exc: BaseException) -> str:
    """Texto seguro para API/CLI: só a classe do erro (nunca str(exc))."""
    return f"Falha de acesso ao banco ({type(exc).__name__})."


# Access log do uvicorn: logger.info('%s - "%s %s HTTP/%s" %d', client_addr, method, full_path,
# http_version, status_code). O AccessFormatter desempacota record.args. Renderizar a mensagem e
# zerar args (o caminho dos outros logs) gera TypeError: cannot unpack non-iterable NoneType object
# e a linha de acesso some. Aqui a tupla fica; senha, DSN, CPF e CNPJ são mascarados dentro das strings.
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
    """Mascara senha/DSN e CPF/CNPJ na mensagem, nos args e no traceback. Nunca descarta o registro.

    No access log do uvicorn a tupla de 5 args é preservada (o formatter do uvicorn a desempacota).
    Senha, DSN, CPF e CNPJ no caminho ou na query saem mascarados. Os demais loggers passam pela mesma
    ``mascarar`` depois de a mensagem ser montada.
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
