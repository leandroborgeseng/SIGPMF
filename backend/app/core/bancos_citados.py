"""R2 do #49: bancos citados dentro de um SQL (nomes de 3 ou 4 partes) e acesso remoto.

Detecta `X.dbo.T`, `[X].[dbo].[T]`, `"X".dbo.T`, `X..T` (schema padrão), `X.outro_schema.T` e 4 partes
(servidor vinculado: sempre recusado). OPENQUERY/OPENROWSET/OPENDATASOURCE: sempre recusado.
Literais de texto ('...') são ignorados. Quem chama passa cada banco citado por `autoriza_base`.
"""
import re
import unicodedata

ACESSO_REMOTO_RE = re.compile(r"\bOPEN(?:QUERY|ROWSET|DATASOURCE)\b", re.IGNORECASE)
_LITERAL_RE = re.compile(r"N?'(?:[^']|'')*'")
_ID = r'(?:\[(?:[^\]]|\]\])+\]|"[^"]+"|[A-Za-z_@#][\w@#$]*)'
_PONTO = r"\s*\.\s*"
# id . [id] . id [. id]   (o do meio pode faltar: X..tabela)
_MULTI_RE = re.compile(rf"(?<![\w\]\".])({_ID}){_PONTO}({_ID})?{_PONTO}({_ID})(?:{_PONTO}({_ID}))?")


class AcessoRemotoRecusado(ValueError):
    codigo = "acesso_remoto_recusado"


def _limpa(ident: str) -> str:
    if ident.startswith("[") and ident.endswith("]"):
        return ident[1:-1].replace("]]", "]")
    if ident.startswith('"') and ident.endswith('"'):
        return ident[1:-1]
    return ident


def bancos_citados(sql: str) -> list[str]:
    """Bancos citados por nome de 3 partes (na ordem). Levanta AcessoRemotoRecusado para OPEN* ou nome de 4 partes."""
    texto = unicodedata.normalize("NFKC", str(sql))
    if ACESSO_REMOTO_RE.search(texto):
        raise AcessoRemotoRecusado("Acesso remoto (OPENQUERY/OPENROWSET/OPENDATASOURCE) recusado.")
    texto = _LITERAL_RE.sub("''", texto)
    out: list[str] = []
    for m in _MULTI_RE.finditer(texto):
        if m.group(4):
            raise AcessoRemotoRecusado("Nome de 4 partes (servidor vinculado) recusado.")
        b = _limpa(m.group(1))
        if b not in out:
            out.append(b)
    return out
