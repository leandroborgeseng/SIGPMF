"""Usuário da origem NÃO garantidamente somente leitura: guarda + transação com ROLLBACK + timeout + limite.
Cobre o conector base e as políticas Saúde e Cemitério (sintético, sem conexão real)."""
import pathlib
import re

import pytest

from app.connectors.sqlserver.connector import ConfigSqlServer, SqlServerConnector
from app.connectors.sqlserver.politicas import CEMITERIO, SAUDE, TabelaRecusada
from app.core.sql_guard import ComandoProibido, executa_somente_leitura, garante_somente_leitura

APP = pathlib.Path(__file__).resolve().parents[1] / "app"
REJEITADOS = [
    "INSERT INTO dbo.Pacientes VALUES (1)", "UPDATE dbo.Obitos SET NOME='x'", "DELETE FROM dbo.Pacientes",
    "MERGE dbo.t USING s ON 1=1 WHEN MATCHED THEN DELETE", "TRUNCATE TABLE dbo.Obitos",
    "CREATE TABLE x (c int)", "ALTER TABLE dbo.Pacientes ADD c int", "DROP TABLE dbo.Obitos",
    "EXEC sp_who", "EXECUTE dbo.proc", "SELECT * FROM sys.objects WHERE name = sp_helptext",
    "SELECT * INTO #tmp FROM dbo.Pacientes", "SELECT * INTO nova FROM dbo.Obitos", "SELECT * FROM #tmp",
    "SELECT * FROM ##global", "SELECT 1; DROP TABLE dbo.Obitos", "SELECT 1 -- comentario",
    "SELECT 1 /* x */", "DECLARE @x int", "SET NOCOUNT ON", "SELECT 1 SET NOCOUNT ON", "WAITFOR DELAY '00:00:05'",
    "SELECT * FROM OPENROWSET('SQLNCLI','x','SELECT 1')", "SELECT * FROM OPENQUERY(srv,'SELECT 1')",
    "BEGIN TRAN", "COMMIT", "GRANT SELECT ON t TO u", "USE master", "BACKUP DATABASE x TO DISK='y'",
    "  ", "",
]


class CursorFalso:
    def __init__(self, conn, falhar):
        self.conn, self.falhar = conn, falhar

    def execute(self, sql, params=None):
        self.conn.log.append(("execute", sql))
        if self.falhar:
            raise RuntimeError("timeout simulado")

    def fetchmany(self, n):
        self.conn.log.append(("fetchmany", n))
        return [{"i": i} for i in range(min(n, 500))]


class ConnFalsa:
    def __init__(self, falhar=False):
        self.log, self.falhar = [], falhar

    def cursor(self, as_dict=False):
        return CursorFalso(self, self.falhar)

    def rollback(self):
        self.log.append(("rollback",))

    def close(self):
        self.log.append(("close",))

    def commit(self):
        raise AssertionError("commit nunca pode ser chamado na origem")


@pytest.mark.parametrize("sql", REJEITADOS)
def test_guarda_rejeita_nao_select(sql):
    with pytest.raises(ComandoProibido):
        garante_somente_leitura(sql)


@pytest.mark.parametrize("sql", REJEITADOS[:-2])
def test_rejeitado_nem_chega_ao_cursor(sql):
    conn = ConnFalsa()
    with pytest.raises(ComandoProibido):
        executa_somente_leitura(conn, sql, limite=10)
    assert not any(e[0] == "execute" for e in conn.log)


def test_select_roda_em_transacao_com_rollback_e_limite():
    conn = ConnFalsa()
    linhas = executa_somente_leitura(conn, "SELECT TOP (100) [NOME] FROM [dbo].[Pacientes]", limite=100)
    assert len(linhas) == 100 and conn.log[-1] == ("rollback",) and ("fetchmany", 100) in conn.log


def test_rollback_mesmo_com_erro_ou_timeout():
    conn = ConnFalsa(falhar=True)
    with pytest.raises(RuntimeError):
        executa_somente_leitura(conn, "SELECT 1", limite=1)
    assert conn.log[-1] == ("rollback",)


def _con(limite=100):
    return SqlServerConnector(ConfigSqlServer(host="h", port=1433, database="zz_d_test", user="u", password="p",
                                              timeout=7, amostra_limite=limite))


def test_conexao_com_timeout_de_consulta_e_sem_autocommit(monkeypatch):
    import sys
    import types
    chamado = {}
    fake = types.SimpleNamespace(connect=lambda **kw: chamado.update(kw) or ConnFalsa())
    monkeypatch.setitem(sys.modules, "pymssql", fake)
    con = _con()
    assert con.testar_conexao()["ok"] is True
    # login com SQLSERVER_TIMEOUT; cada consulta com SQLSERVER_CONSULTA_TIMEOUT (default 120)
    assert chamado["timeout"] == 120 and chamado["login_timeout"] == 7 and chamado["autocommit"] is False


@pytest.mark.parametrize("pol", [SAUDE, CEMITERIO], ids=["saude", "cemiterio"])
def test_politica_gera_so_select_com_limite_configuravel(pol, monkeypatch):
    import sys
    import types
    conns = []

    def conecta(**kw):
        conns.append(ConnFalsa())
        return conns[-1]
    monkeypatch.setitem(sys.modules, "pymssql", types.SimpleNamespace(connect=conecta))
    for limite in (100, 25):
        con = _con(limite)
        for tabela in pol.colunas:
            sql = con.montar_select_politica(pol, tabela)
            assert garante_somente_leitura(sql) == sql and f"TOP ({limite})" in sql
            con.ler_amostra(tabela, politica=pol)
            assert conns[-1].log[0][0] == "execute" and conns[-1].log[-2:] == [("rollback",), ("close",)]
            assert ("fetchmany", limite) in conns[-1].log


@pytest.mark.parametrize("pol", [SAUDE, CEMITERIO], ids=["saude", "cemiterio"])
@pytest.mark.parametrize("tabela", ["Pacientes; DROP TABLE x", "Obitos--", "#tmp", "Pacientes WHERE 1=1",
                                    "sp_who", "[Obitos]"])
def test_nome_vindo_de_texto_do_usuario_recusado(pol, tabela):
    with pytest.raises((TabelaRecusada, ValueError)):
        _con().montar_select_politica(pol, tabela)


@pytest.mark.parametrize("pol,tabela", [(SAUDE, t) for t in ("Lab_UsuariosWeb", "Cad_UsuariosWeb", "2APAGAR_X",
                                                                 "Pacientes_backup")]
                         + [(CEMITERIO, t) for t in ("OBITO01(CONGELADO)", "Novo_Cemiterio", "USUARIO")])
def test_padroes_de_recusa_explicitos(pol, tabela):
    with pytest.raises(TabelaRecusada, match="recusada pela política"):
        pol.autoriza(tabela)


def test_cursor_execute_so_dentro_da_guarda():
    """Nenhum .execute( contra a origem fora de app/core/sql_guard.py (conectores e ingestão)."""
    achados = []
    for pasta in ("connectors", "ingestion"):
        for arq in (APP / pasta).rglob("*.py"):
            if re.search(r"\.execute\(|\.executemany\(|callproc\(", arq.read_text(encoding="utf-8")):
                achados.append(str(arq))
    assert achados == []
