"""Guarda de somente leitura para SQL enviado às origens."""
import re

COMANDOS_PROIBIDOS = ("INSERT", "UPDATE", "DELETE", "MERGE", "TRUNCATE", "EXEC", "EXECUTE", "CREATE", "ALTER",
                      "DROP", "GRANT", "REVOKE", "DENY", "BULK", "DBCC", "BACKUP", "RESTORE", "USE")
PROIBIDO_RE = re.compile(r"\b(" + "|".join(COMANDOS_PROIBIDOS) + r")\b", re.IGNORECASE)
SELECT_INTO_RE = re.compile(r"\bSELECT\b[\s\S]*\bINTO\b", re.IGNORECASE)


class ComandoProibido(ValueError):
    pass


def garante_somente_leitura(sql: str) -> str:
    texto = sql.strip()
    inicio = texto.split(None, 1)[0].upper() if texto else ""
    if inicio not in ("SELECT", "WITH") or ";" in texto or PROIBIDO_RE.search(texto) or SELECT_INTO_RE.search(texto):
        raise ComandoProibido("O conector só executa um único SELECT.")
    return texto
