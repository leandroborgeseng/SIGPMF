"""Diagnóstico de estrutura: política x INFORMATION_SCHEMA (mock sintético) + nenhuma tabela AAA_NAO_UTILIZADO."""
import sys
import types

import pytest

from app.connectors.sqlserver.politicas import CEMITERIO, ISS, POLITICAS, SAUDE, TabelaRecusada

HOST, USER, PWD = "srv-sintetico.invalid", "usuario_sintetico", "SenhaSintetica#123"


def test_nenhuma_politica_tem_tabela_aaa_nao_utilizado():
    for p in POLITICAS.values():
        ruins = [t for t in p.colunas if t.upper().startswith("AAA_NAO_UTILIZADO")]
        assert not ruins, (p.sistema, ruins)


def test_aaa_nao_utilizado_recusada_pela_politica():
    for t in ("AAA_NAO_UTILIZADO_OBITO01", "AAA_NAO_UTILIZADO_OBITO01_Sepulta_Anterior1982", "AAA_NAO_UTILIZADO_x"):
        with pytest.raises(TabelaRecusada):
            CEMITERIO.colunas_select(t)
    assert "Obitos" in CEMITERIO.colunas and "Titulares" in CEMITERIO.colunas  # resto intacto


def _catalogo(pol, sem_tabela=(), sem_coluna=None):
    """INFORMATION_SCHEMA sintético a partir da política, com remoções controladas."""
    sem_coluna = sem_coluna or {}
    tabs = [t for t in pol.colunas if t not in sem_tabela] + ["OutraTabela"]
    cols = [(t.upper(), c.lower()) for t in tabs if t in pol.colunas for c in pol.colunas[t]
            if c not in sem_coluna.get(t, ())]
    return tabs, cols


class Origem:
    def __init__(self, cats):
        self.cats, self.sqls, self.params = cats, [], []

    def connect(self, **kw):
        origem, db = self, kw["database"]

        class Cur:
            def execute(self, sql, params=None):
                origem.sqls.append(sql)
                origem.params.append(params)
                self.sql = sql

            def fetchmany(self, n):
                tabs, cols = origem.cats[db]
                if "INFORMATION_SCHEMA.TABLES" in self.sql:
                    return [(t,) for t in tabs][:n]
                if "INFORMATION_SCHEMA.COLUMNS" in self.sql:
                    return cols[:n]
                return [(1, db)]

        class Conn:
            def cursor(self, as_dict=False):
                return Cur()

            def rollback(self):
                origem.sqls.append("ROLLBACK")

            def close(self):
                pass
        return Conn()


@pytest.fixture
def origem(monkeypatch):
    cats = {"Saude": _catalogo(SAUDE),
            "Cemiterio": _catalogo(CEMITERIO, sem_tabela=("Leilao",), sem_coluna={"Titulares": ("Endereco", "Ativo")}),
            "ISS": _catalogo(ISS, sem_coluna={"Municipes": ("Documento_SoNumeros",)})}
    o = Origem(cats)
    monkeypatch.setitem(sys.modules, "pymssql", types.SimpleNamespace(connect=o.connect))
    for k, v in {"SQLSERVER_HOST": HOST, "SQLSERVER_USER": USER, "SQLSERVER_PASSWORD": PWD,
                 "SQLSERVER_DATABASE_SAUDE": "Saude", "SQLSERVER_DATABASE_CEMITERIO": "Cemiterio",
                 "SQLSERVER_DATABASE_ISS": "ISS"}.items():
        monkeypatch.setenv(k, v)
    from app.core.config import get_settings
    get_settings.cache_clear()
    yield o
    get_settings.cache_clear()


def test_diagnostico_todos_lista_faltas_por_conector(client, tokens, origem):
    assert client.post("/api/conectores/diagnostico").status_code == 401
    assert client.post("/api/conectores/diagnostico", headers=tokens["operador"]).status_code == 403
    r = client.post("/api/conectores/diagnostico", headers=tokens["admin"])
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["ok"] is False
    c = d["conectores"]
    assert c["saude"]["ok"] is True and c["saude"]["estrutura"]["tabelas_faltando"] == []
    assert c["cemiterio"]["estrutura"]["tabelas_faltando"] == ["Leilao"]
    assert c["cemiterio"]["estrutura"]["colunas_faltando"] == {"Titulares": ["Endereco", "Ativo"]}
    assert c["iss"]["estrutura"]["colunas_faltando"] == {"Municipes": ["Documento_SoNumeros"]}
    assert c["cemiterio"]["banco"] == "Cemiterio"
    for t in (r.text,):
        assert HOST not in t and USER not in t and PWD not in t


def test_diagnostico_usa_guard_parametros_e_rollback(client, tokens, origem):
    from app.core.sql_guard import garante_somente_leitura
    client.post("/api/conectores/cemiterio/diagnostico", headers=tokens["admin"])
    meta = [s for s in origem.sqls if "INFORMATION_SCHEMA" in s]
    assert meta and all(s.startswith("SELECT TOP (") and garante_somente_leitura(s) == s for s in meta)
    assert origem.sqls.count("ROLLBACK") >= 3  # cada consulta termina em ROLLBACK
    assert all("Titulares" not in s for s in meta)  # nomes vão como parâmetro, não no texto
    assert any(p and "Titulares" in p for p in origem.params)
