"""A10 / S3 / CM2: o conector SQL Server só emite SELECT.

`test_somente_leitura_codigo_do_conector` varre o pacote app/connectors e FALHA se algum literal de string
contiver comando de escrita (INSERT, UPDATE, DELETE, MERGE, TRUNCATE, EXEC, DDL, GRANT, SELECT INTO).
Teste por mutação (CON-04): plante, por exemplo, `X = "MERGE t USING s"` em connector.py e rode
`pytest -k somente_leitura`; o teste falha.
"""
import ast
import pathlib

import pytest

from app.connectors.sqlserver.connector import ConfigSqlServer, SqlServerConnector
from app.core.sql_guard import PROIBIDO_RE, SELECT_INTO_RE, ComandoProibido, garante_somente_leitura

PACOTE = pathlib.Path(__file__).resolve().parents[1] / "app" / "connectors"
PROIBIDOS = ["INSERT INTO t VALUES (1)", "UPDATE x SET y=1", "DELETE FROM t", "MERGE t USING s ON 1=1",
             "TRUNCATE TABLE t", "EXEC sp_x", "EXECUTE sp_x", "DROP TABLE t", "ALTER TABLE t ADD c int",
             "CREATE TABLE t (c int)", "GRANT SELECT ON t TO u", "SELECT * INTO nova FROM t", "SELECT 1; DROP TABLE t"]


def _literais_proibidos(fonte: str) -> list[str]:
    achados = []
    for no in ast.walk(ast.parse(fonte)):
        if isinstance(no, ast.Constant) and isinstance(no.value, str):
            if PROIBIDO_RE.search(no.value) or SELECT_INTO_RE.search(no.value):
                achados.append(no.value)
    return achados


def test_somente_leitura_codigo_do_conector():
    achados = {}
    for arq in PACOTE.rglob("*.py"):
        lits = _literais_proibidos(arq.read_text(encoding="utf-8"))
        if lits:
            achados[str(arq)] = lits
    assert achados == {}, f"Comando de escrita no conector: {achados}"


@pytest.mark.parametrize("sql", PROIBIDOS)
def test_somente_leitura_varredura_detecta_mutacao(sql):
    # o detector usado acima pega cada comando proibido plantado
    assert _literais_proibidos(f"X = {sql!r}")


@pytest.mark.parametrize("sql", PROIBIDOS)
def test_somente_leitura_guarda_recusa(sql):
    with pytest.raises(ComandoProibido):
        garante_somente_leitura(sql)


def _con(limite=100):
    return SqlServerConnector(ConfigSqlServer(None, 1433, None, None, None, 2, limite), nome="DEMO_SAUDE")


def test_somente_leitura_select_gerado_e_amostra_padrao():
    sql = _con().montar_select("dbo.pacientes", ["Matricula", "Nome"])
    assert sql.startswith("SELECT TOP (100) ")
    assert _con(20).montar_select("dbo.pacientes", limite=500).startswith("SELECT TOP (20) ")


def test_somente_leitura_consultas_capturadas_em_mock():
    emitidas = []

    class Cur:
        def execute(self, sql, params=None):
            emitidas.append(sql)

        def fetchone(self):
            return (1,)

    class Conn:
        def cursor(self, **kw):
            return Cur()

        def close(self):
            pass

    c = SqlServerConnector(ConfigSqlServer("h", 1433, "d", "u", "p", 2, 100), nome="DEMO")
    c._conectar = lambda: Conn()
    assert c.testar_conexao()["ok"]
    assert emitidas and all(s.split()[0].upper() in ("SELECT", "WITH") for s in emitidas)


def test_origem_invalida_erro_claro_sem_segredo():
    cfg = ConfigSqlServer("10.255.255.1", 1433, "BaseFicticia", "usuario_ficticio", "SenhaFicticia#1", 2, 100)
    r = SqlServerConnector(cfg, nome="DEMO_CEMITERIO").testar_conexao()
    assert r["ok"] is False
    assert r["erro"].startswith("Falha ao conectar em DEMO_CEMITERIO")
    for segredo in ("10.255.255.1", "usuario_ficticio", "SenhaFicticia#1", "BaseFicticia", "1433"):
        assert segredo not in r["erro"]
