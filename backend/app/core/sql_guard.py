"""Guarda de somente leitura para SQL enviado às origens.

O usuário SQL das origens NÃO é garantidamente somente leitura. Por isso:
- só passa um único SELECT/WITH, sem DML, DDL, EXEC/procedures, tabelas temporárias, SELECT INTO,
  OPENROWSET/OPENQUERY, comentários ou múltiplos comandos;
- `executa_somente_leitura` é o ÚNICO ponto que chama cursor.execute contra a origem: roda dentro de uma
  transação que termina SEMPRE em ROLLBACK (mesmo com sucesso ou erro), com timeout de consulta.
"""
import re
from typing import Any

COMANDOS_PROIBIDOS = ("INSERT", "UPDATE", "DELETE", "MERGE", "TRUNCATE", "EXEC", "EXECUTE", "CREATE", "ALTER",
                      "DROP", "GRANT", "REVOKE", "DENY", "BULK", "DBCC", "BACKUP", "RESTORE", "USE", "DECLARE",
                      "SET", "WAITFOR", "OPENROWSET", "OPENQUERY", "OPENDATASOURCE", "OPENXML", "SHUTDOWN", "KILL",
                      "RECONFIGURE", "COMMIT", "BEGIN", "SAVE", "CALL")
PROIBIDO_RE = re.compile(r"\b(" + "|".join(COMANDOS_PROIBIDOS) + r")\b", re.IGNORECASE)
SELECT_INTO_RE = re.compile(r"\bSELECT\b[\s\S]*\bINTO\b", re.IGNORECASE)
EXTRA_RE = re.compile(r"#|--|/\*|\b(sp|xp)_\w+", re.IGNORECASE)  # temp tables, comentários, procedures


class ComandoProibido(ValueError):
    pass


def garante_somente_leitura(sql: str) -> str:
    texto = sql.strip()
    inicio = texto.split(None, 1)[0].upper() if texto else ""
    if inicio not in ("SELECT", "WITH") or ";" in texto or PROIBIDO_RE.search(texto) or SELECT_INTO_RE.search(texto) \
            or EXTRA_RE.search(texto):
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
