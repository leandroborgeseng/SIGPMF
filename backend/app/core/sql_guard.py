"""Guarda de somente leitura para SQL enviado às origens.

O usuário SQL das origens NÃO é garantidamente somente leitura. Por isso:
- só passa um único SELECT/WITH, sem DML, DDL, EXEC/procedures, tabelas temporárias, SELECT INTO,
  OPENROWSET/OPENQUERY, comentários, múltiplos comandos,
  hints de lock (UPDLOCK, HOLDLOCK, XLOCK...) nem `*` na lista de colunas (exceto COUNT(*));
- `executa_somente_leitura` é o ÚNICO ponto que chama cursor.execute contra a origem: roda dentro de uma
  transação que termina SEMPRE em ROLLBACK (mesmo com sucesso ou erro), com timeout de consulta.
"""
import re
import unicodedata
from typing import Any

COMANDOS_PROIBIDOS = ("INSERT", "UPDATE", "DELETE", "MERGE", "TRUNCATE", "EXEC", "EXECUTE", "CREATE", "ALTER",
                      "DROP", "GRANT", "REVOKE", "DENY", "BULK", "DBCC", "BACKUP", "RESTORE", "USE", "DECLARE",
                      "SET", "WAITFOR", "OPENROWSET", "OPENQUERY", "OPENDATASOURCE", "OPENXML", "SHUTDOWN", "KILL",
                      "RECONFIGURE", "COMMIT", "BEGIN", "SAVE", "CALL")
PROIBIDO_RE = re.compile(r"\b(" + "|".join(COMANDOS_PROIBIDOS) + r")\b", re.IGNORECASE)
SELECT_INTO_RE = re.compile(r"\bSELECT\b[\s\S]*\bINTO\b", re.IGNORECASE)
EXTRA_RE = re.compile(r"#|--|/\*|\b(sp|xp)_\w+", re.IGNORECASE)  # temp tables, comentários, procedures
# Hints de lock seguram travas na origem até o ROLLBACK: recusados (ressalva 3 do QA do PR #3).
HINTS_LOCK = ("UPDLOCK", "HOLDLOCK", "XLOCK", "TABLOCK", "TABLOCKX", "PAGLOCK", "ROWLOCK", "SERIALIZABLE",
              "REPEATABLEREAD")
HINT_LOCK_RE = re.compile(r"\b(" + "|".join(HINTS_LOCK) + r")\b", re.IGNORECASE)
# `*` na lista de colunas é recusado (ressalva 1); só COUNT(*) é aceito.
COUNT_STAR_RE = re.compile(r"\bCOUNT\s*\(\s*\*\s*\)", re.IGNORECASE)


# Caracteres invisíveis (largura zero, BOM, controles bidi, hífen suave...) escondem palavras proibidas
# (ex.: "UPD\u200bLOCK"): qualquer caractere de formato (categoria Unicode Cf) é recusado (ressalva do QA do #4).
INVISIVEIS_EXPLICITOS = frozenset("\u00ad\u180e\u200b\u200c\u200d\u200e\u200f\u2060\u2061\u2062\u2063\u2064"
                                  "\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069\ufeff")


def tem_invisivel(texto: str) -> bool:
    return any(c in INVISIVEIS_EXPLICITOS or unicodedata.category(c) == "Cf" for c in texto)


class ComandoProibido(ValueError):
    pass


def garante_somente_leitura(sql: str) -> str:
    if tem_invisivel(sql):
        raise ComandoProibido("O conector só executa um único SELECT (caractere invisível recusado).")
    texto = sql.strip()
    inicio = texto.split(None, 1)[0].upper() if texto else ""
    if inicio not in ("SELECT", "WITH") or ";" in texto or PROIBIDO_RE.search(texto) or SELECT_INTO_RE.search(texto) \
            or EXTRA_RE.search(texto) or HINT_LOCK_RE.search(texto) or "*" in COUNT_STAR_RE.sub("", texto):
        raise ComandoProibido("O conector só executa um único SELECT.")
    return texto


def executa_somente_leitura(conn, sql: str, params: tuple | None = None, *, limite: int,
                            como_dict: bool = True) -> list[Any]:
    """Executa um SELECT validado numa transação que termina sempre em ROLLBACK; no máximo `limite` linhas.
    O timeout de consulta vem da conexão (pymssql `timeout=`), aberta pelo conector."""
    texto = garante_somente_leitura(sql)
    try:
        cur = conn.cursor(as_dict=True) if como_dict else conn.cursor()
        cur.execute(texto, params)
        return list(cur.fetchmany(int(limite)))
    finally:
        conn.rollback()
