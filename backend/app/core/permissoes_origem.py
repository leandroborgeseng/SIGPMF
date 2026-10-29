"""R1 do #51: a credencial da origem SQL Server não pode escrever.

`permissoes_de_escrita` / `garante_sem_escrita` consultam `fn_my_permissions`, que devolve as permissões EFETIVAS do
login no banco (e, opcionalmente, em cada objeto). Assim pegam permissão herdada de papel fixo (db_datawriter,
db_owner, db_ddladmin), concedida ao `public` e vinda de papel customizado, e não só GRANT direto ao usuário.
A consulta é só SELECT e passa pelo guard (`executa_somente_leitura`: ROLLBACK, timeout, limite).
"""
from collections.abc import Iterable

# Permissões que, se efetivas, deixam a credencial alterar dados ou estrutura.
PERMISSOES_ESCRITA = frozenset({
    "INSERT", "UPDATE", "DELETE", "ALTER", "CONTROL", "TAKE OWNERSHIP", "EXECUTE", "REFERENCES",
    "CREATE TABLE", "CREATE VIEW", "CREATE PROCEDURE", "CREATE FUNCTION", "CREATE SCHEMA", "CREATE TYPE",
    "ALTER ANY SCHEMA", "ALTER ANY USER", "ALTER ANY ROLE", "ALTER ANY DATABASE DDL TRIGGER", "IMPERSONATE",
    "BACKUP DATABASE", "BACKUP LOG"})

SQL_PERMISSOES_BANCO = "SELECT [permission_name] FROM fn_my_permissions(NULL, 'DATABASE')"
SQL_PERMISSOES_OBJETO = "SELECT [permission_name] FROM fn_my_permissions(%s, 'OBJECT')"


class CredencialComEscrita(PermissionError):
    codigo = "credencial_com_escrita"


def _nomes(linhas) -> set[str]:
    out = set()
    for ln in linhas or ():
        v = ln.get("permission_name") if isinstance(ln, dict) else ln[0]
        if v:
            out.add(str(v).strip().upper())
    return out


def permissoes_de_escrita(conn, objetos: Iterable[str] = (), executor=None) -> list[str]:
    """Permissões de escrita efetivas no banco e em cada objeto (ex.: "dbo.Tabela"), ordenadas. Lista vazia = ok."""
    if executor is None:
        from app.core.sql_guard import executa_somente_leitura as executor
    achadas = {f"DATABASE:{p}" for p in _nomes(executor(conn, SQL_PERMISSOES_BANCO, limite=1000)) & PERMISSOES_ESCRITA}
    for obj in objetos:
        nomes = _nomes(executor(conn, SQL_PERMISSOES_OBJETO, (obj,), limite=1000))
        achadas |= {f"{obj}:{p}" for p in nomes & PERMISSOES_ESCRITA}
    return sorted(achadas)


def garante_sem_escrita(conn, objetos: Iterable[str] = (), executor=None) -> None:
    achadas = permissoes_de_escrita(conn, objetos, executor)
    if achadas:
        raise CredencialComEscrita(f"Credencial da origem tem permissão de escrita: {', '.join(achadas)}")
