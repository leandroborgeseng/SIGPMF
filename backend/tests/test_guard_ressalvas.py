"""Ressalvas do QA do PR #3: sem SELECT *, limite >= 1 e sem hints de lock. Só dados sintéticos, sem origem real."""
from types import SimpleNamespace

import pytest

from app.connectors.sqlserver.connector import ConfigSqlServer, SqlServerConnector
from app.core.sql_guard import ComandoProibido, executa_somente_leitura, garante_somente_leitura


def _con(maximo=100):
    return SqlServerConnector(ConfigSqlServer(None, 1433, None, None, None, 2, maximo), nome="DEMO")


# 1) SELECT *
@pytest.mark.parametrize("colunas", [None, []])
def test_montar_select_sem_colunas_recusa(colunas):
    with pytest.raises(ValueError, match="colunas"):
        _con().montar_select("dbo.pacientes", colunas)


def test_politica_sem_colunas_recusa():
    pol = SimpleNamespace(colunas_select=lambda t: ())
    with pytest.raises(ValueError, match="colunas"):
        _con().montar_select_politica(pol, "dbo.pacientes")


@pytest.mark.parametrize("sql", ["SELECT * FROM t", "SELECT TOP (5) * FROM [dbo].[t]", "select t.* from t",
                                 "WITH x AS (SELECT a FROM t) SELECT * FROM x", "SELECT a,\t* FROM t"])
def test_guard_recusa_star(sql):
    with pytest.raises(ComandoProibido):
        garante_somente_leitura(sql)


def test_guard_aceita_count_star():
    assert garante_somente_leitura("SELECT COUNT( * ) FROM t")


# 2) limite
@pytest.mark.parametrize("limite", [0, -1, -1000, True, 1.5, "10"])
def test_montar_select_limite_invalido(limite):
    with pytest.raises(ValueError, match="Limite"):
        _con().montar_select("dbo.t", ["a"], limite=limite)


def test_montar_select_limite_valido_e_truncado():
    assert _con().montar_select("dbo.t", ["a"], limite=1).startswith("SELECT TOP (1) ")
    assert _con(50).montar_select("dbo.t", ["a"], limite=999999).startswith("SELECT TOP (50) ")
    assert _con(50).montar_select("dbo.t", ["a"]).startswith("SELECT TOP (50) ")


# 3) hints de lock
HINTS = ["UPDLOCK", "HOLDLOCK", "XLOCK", "TABLOCK", "TABLOCKX", "PAGLOCK", "ROWLOCK", "SERIALIZABLE",
         "REPEATABLEREAD"]


@pytest.mark.parametrize("hint", HINTS + [h.lower() for h in HINTS] + ["UpdLock", "holdLock"])
@pytest.mark.parametrize("ws", [" ", "\t", "\n", "\u00a0", "  \r\n "])
def test_guard_recusa_hints_lock(hint, ws):
    with pytest.raises(ComandoProibido):
        garante_somente_leitura(f"SELECT a FROM t{ws}WITH{ws}({ws}{hint}{ws})")


def test_guard_recusa_hint_combinado():
    with pytest.raises(ComandoProibido):
        garante_somente_leitura("SELECT a FROM t WITH (NOLOCK, UPDLOCK)")


def test_guard_aceita_nolock_e_coluna_parecida():
    assert garante_somente_leitura("SELECT a FROM t WITH (NOLOCK)")
    assert garante_somente_leitura("SELECT [rowlock_x], updlocked FROM t")


def test_executa_recusa_hint_sem_abrir_cursor():
    class Conn:
        cursor_aberto = False
        def cursor(self, **_):
            Conn.cursor_aberto = True
        def rollback(self):
            pass
    with pytest.raises(ComandoProibido):
        executa_somente_leitura(Conn(), "SELECT a FROM t WITH (XLOCK)", limite=1)
    assert not Conn.cursor_aberto
