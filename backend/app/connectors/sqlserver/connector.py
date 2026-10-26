"""Conector SQL Server SOMENTE LEITURA.

Só monta e executa SELECT; tudo passa por `app.core.sql_guard.garante_somente_leitura`.
O teste tests/test_conector_somente_leitura.py varre este pacote e falha se surgir comando de escrita.
O usuário da origem pode não ser somente leitura: cada consulta roda em transação com ROLLBACK e timeout.
Nesta entrega não há conexão com origem real.
"""
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.connectors.sqlserver.politicas import TabelaRecusada
from app.core.config import Settings
from app.core.sql_guard import executa_somente_leitura, garante_somente_leitura

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
def _ident(nome: str) -> str:
    partes = nome.split(".")
    if not 1 <= len(partes) <= 2 or not all(_IDENT.match(p) for p in partes):
        raise ValueError(f"Identificador inválido: {nome!r}")
    return ".".join(f"[{p}]" for p in partes)


@dataclass
class ConfigSqlServer:
    host: str | None
    port: int
    database: str | None
    user: str | None
    password: str | None
    timeout: int
    amostra_limite: int

    @classmethod
    def do_ambiente(cls, s: Settings) -> "ConfigSqlServer":
        return cls(s.sqlserver_host, s.sqlserver_port, s.sqlserver_database, s.sqlserver_user,
                   s.sqlserver_password, s.sqlserver_timeout, s.amostra_limite)

    def resumo_publico(self) -> dict[str, Any]:
        """Sem senha nem usuário."""
        return {"host": self.host, "port": self.port, "database": self.database,
                "configurado": bool(self.host and self.database and self.user),
                "amostra_limite": self.amostra_limite}


class SqlServerConnector:
    def __init__(self, config: ConfigSqlServer, nome: str | None = None):
        self.config = config
        self.nome = nome

    def montar_select(self, tabela: str, colunas: list[str] | None = None, campo_incremental: str | None = None,
                      limite: int | None = None) -> str:
        maximo = int(self.config.amostra_limite)
        n = maximo if limite is None else limite
        if isinstance(n, bool) or not isinstance(n, int) or n < 1:
            raise ValueError(f"Limite inválido: {limite!r} (mínimo 1).")
        n = min(n, maximo)  # acima do máximo configurado: trunca no máximo
        if not colunas:
            raise ValueError(f"Lista de colunas obrigatória para {tabela!r}: SELECT * não é permitido.")
        cols = ", ".join(_ident(c) for c in colunas)
        sql = f"SELECT TOP ({int(n)}) {cols} FROM {_ident(tabela)}"
        if campo_incremental:
            sql += f" WHERE {_ident(campo_incremental)} > %s ORDER BY {_ident(campo_incremental)}"
        return garante_somente_leitura(sql)

    def _conectar(self):
        import pymssql  # import tardio: o driver só é necessário com origem real
        c = self.config
        return pymssql.connect(server=c.host, port=c.port, user=c.user, password=c.password,
                               database=c.database, login_timeout=c.timeout, timeout=c.timeout,
                               autocommit=False)  # transação explícita: sempre ROLLBACK (sql_guard)

    def testar_conexao(self) -> dict[str, Any]:
        if not self.config.resumo_publico()["configurado"]:
            return {"ok": False, "erro": f"Falha ao conectar em {self.nome or 'a origem'}: origem não configurada no ambiente."}
        try:
            conn = self._conectar()
            try:
                executa_somente_leitura(conn, "SELECT 1", limite=1, como_dict=False)
            finally:
                conn.close()
            return {"ok": True, "erro": None}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "erro": self._erro_limpo(exc)}

    def _erro_limpo(self, exc: Exception) -> str:
        """Mensagem clara SEM senha, usuário, host nem string de conexão."""
        nome = self.nome or "a origem"
        if isinstance(exc, (TimeoutError, OSError)) or "timeout" in type(exc).__name__.lower():
            return f"Falha ao conectar em {nome}: servidor não respondeu. Verifique a configuração do ambiente."
        return f"Falha ao conectar em {nome}: servidor não respondeu ou recusou a conexão. Verifique a configuração do ambiente."

    def montar_select_politica(self, politica, tabela: str, limite: int | None = None,
                               campo_incremental: str | None = None) -> str:
        """SELECT só com as colunas permitidas pela política do sistema (recusa tabela fora da lista)."""
        cols = list(politica.colunas_select(tabela))
        if campo_incremental and campo_incremental not in cols:
            raise ValueError(f"Campo incremental {campo_incremental!r} fora das colunas da política.")
        return self.montar_select(f"dbo.{tabela.split('.')[-1]}", cols, campo_incremental=campo_incremental,
                                  limite=limite)

    def ler_amostra(self, tabela: str, politica, campo_incremental: str | None = None,
                    desde: datetime | None = None) -> list[dict[str, Any]]:
        """Lê no máximo AMOSTRA_LIMITE linhas, sempre com a política do sistema (lista de colunas explícita)."""
        if politica is None:
            raise TabelaRecusada(f"Leitura de {tabela!r} exige a política do sistema.")
        sql = self.montar_select_politica(politica, tabela, campo_incremental=campo_incremental if desde else None)
        conn = self._conectar()
        try:
            return executa_somente_leitura(conn, sql, (desde,) if desde else None, limite=self.config.amostra_limite)
        finally:
            conn.close()
