"""Saúde em produção: "Erro na origem (MSSQLDriverException)" na assinatura da agenda.

Causa: consulta pesada (COUNT/MAX em tabela grande) estoura o timeout, o driver derruba a conexão e o ROLLBACK do guard
falha com "Not connected" — esse segundo erro escondia o verdadeiro. Aqui: o erro original sobrevive, o timeout de
consulta é separado do de login, e uma tabela lenta não derruba a assinatura do conector inteiro.
"""
import pytest

from app.connectors.sqlserver.connector import ConfigSqlServer, SqlServerConnector
from app.core.sql_guard import executa_somente_leitura
from app.services import agenda as ag
from app.services import carga_conector as cc
from app.services.diagnostico import FalhaLeitura, motivo


class _MSSQLDriverException(Exception):
    pass


_MSSQLDriverException.__name__ = "MSSQLDriverException"


class _CursorQueEstoura:
    def execute(self, sql, params):
        raise TimeoutError("timeout sintético")


class _ConexaoMorta:
    def cursor(self, **kw):
        return _CursorQueEstoura()

    def rollback(self):
        raise _MSSQLDriverException("Not connected to any MS SQL server")


def test_guard_preserva_o_erro_original_quando_o_rollback_falha():
    with pytest.raises(TimeoutError):
        executa_somente_leitura(_ConexaoMorta(), "SELECT 1", limite=1, como_dict=False)


def test_timeout_de_consulta_separado_do_login(monkeypatch):
    import pymssql
    visto = {}
    monkeypatch.setattr(pymssql, "connect", lambda **kw: visto.update(kw) or object())
    SqlServerConnector(ConfigSqlServer("h", 1433, "zz_d_test", "u", "p", 5, 100, consulta_timeout=120))._conectar()
    assert visto["login_timeout"] == 5 and visto["timeout"] == 120
    # construtor posicional antigo continua valendo (default 120)
    assert ConfigSqlServer("h", 1433, "zz_d_test", "u", "p", 5, 100).consulta_timeout == 120


class _Leitor:
    def __init__(self, lentas):
        self.lentas, self.aberturas = set(lentas), 0

    def abrir(self):
        self.aberturas += 1
        return type("C", (), {"close": lambda self: None})()

    def assinatura(self, conn, tabela, pol, campo):
        if tabela in self.lentas:
            raise TimeoutError("lenta")
        return [10, "2026-10-28"]


def test_tabela_lenta_vira_indisponivel_e_nao_derruba_o_conector():
    tabelas = cc.tabelas_do_conector("SAUDE")
    lenta = "Consultas" if "Consultas" in tabelas else tabelas[0]
    leitor = _Leitor([lenta])
    r = ag.assinatura_origem(leitor, "SAUDE")
    assert r[lenta] == [None, "indisponivel"]
    assert all(v == [10, "2026-10-28"] for t, v in r.items() if t != lenta)
    assert leitor.aberturas == 2            # reabriu a conexão depois da falha
    assert ag._tem_indisponivel(r)          # com tabela indisponível a agenda NÃO pula a carga


def test_todas_falhando_levanta_com_a_tabela_na_mensagem():
    tabelas = cc.tabelas_do_conector("SAUDE")
    with pytest.raises(FalhaLeitura) as e:
        ag.assinatura_origem(_Leitor(tabelas), "SAUDE")
    assert e.value.tabela == tabelas[0]


def test_motivo_explica_conexao_caida():
    m = motivo("MSSQLDriverException", None, "Consultas", None)
    assert "caiu" in m and "Consultas" in m and "SQLSERVER_CONSULTA_TIMEOUT" in m
