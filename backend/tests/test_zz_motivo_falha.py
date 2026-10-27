"""Reprodução dos erros de produção pelo caminho real (/amostra -> SqlServerConnector -> pymssql falso).
Só dados sintéticos; nenhuma origem real."""
import datetime as dt
import re
import sys
import types
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.models.camadas import RegistroBronze
from app.models.controle import Execucao

HOST, USER, PWD = "srv-sintetico.invalid", "usuario_sintetico", "SenhaSintetica#123"


class ProgrammingError(Exception):
    pass


def _valor(col: str, i: int):
    c = col.lower()
    if c in ("normalizado", "matricula_correta"):
        return b"\x01" if i else None                      # bit/varbinary -> bytes
    if any(k in c for k in ("dt", "data", "inclusao", "dt_morte", "sepultado", "liberacao", "venda")):
        return dt.datetime(2020, 1, 2, 3, 4, 5) + dt.timedelta(days=i)
    if c in ("idade", "nr", "cod_cemiterio", "codcemite", "sequencia", "gaveta", "lado"):
        return Decimal(10 + i)
    if c == "tipo_pessoa":
        return "F"
    if c in ("cpf", "documento_sonumeros"):
        from app.identity.cpf import digitos_verificadores
        b = f"{987654300 + i:09d}"
        return b + digitos_verificadores(b)
    return f"SINTETICO {col} {i}"


class Origem:
    def __init__(self, faltando=None, vazio=False):
        self.faltando, self.vazio, self.kw, self.sqls = faltando or {}, vazio, None, []

    def connect(self, **kw):
        self.kw = kw
        origem = self

        class Cur:
            def execute(self, sql, params=None):
                origem.sqls.append(sql)
                m = re.search(r"FROM \[dbo\]\.\[(\w+)\]", sql)
                self.tab = m.group(1) if m else None
                cols = re.findall(r"\[(\w+)\]", sql.split(" FROM ")[0])
                if self.tab in origem.faltando:
                    col = origem.faltando[self.tab]
                    raise ProgrammingError(207, f"Invalid column name '{col}'.DB-Lib error message 20018, severity 16:\n"
                                                f"General SQL Server error: Check messages from the SQL Server ({HOST})\n".encode())
                self.cols = cols

            def fetchmany(self, n):
                if origem.vazio or self.tab is None:
                    return [] if origem.vazio else [(1, "Saude")]
                return [{c: _valor(c, i) for c in self.cols} for i in range(3)]

        class Conn:
            def cursor(self, as_dict=False):
                return Cur()

            def rollback(self):
                pass

            def close(self):
                pass
        return Conn()


@pytest.fixture
def origem(monkeypatch):
    o = Origem()
    monkeypatch.setitem(sys.modules, "pymssql", types.SimpleNamespace(connect=o.connect, ProgrammingError=ProgrammingError))
    # Coolify entrega tudo como texto
    for k, v in {"SQLSERVER_HOST": HOST, "SQLSERVER_USER": USER, "SQLSERVER_PASSWORD": PWD, "SQLSERVER_PORT": "1433",
                 "SQLSERVER_TIMEOUT": "15", "AMOSTRA_LIMITE": "100", "SQLSERVER_DATABASE": "Geral"}.items():
        monkeypatch.setenv(k, v)
    from app.core.config import get_settings
    get_settings.cache_clear()
    yield o
    get_settings.cache_clear()


def _ex(db, r):
    db.expire_all()
    return db.get(Execucao, r.json()["id"])


def test_saude_tipos_reais_do_driver(client, tokens, db, origem):
    r = client.post("/api/conectores/saude/amostra", headers=tokens["admin"])
    assert r.status_code == 202, r.text
    ex = _ex(db, r)
    assert ex.status in ("sucesso", "sem_novidades"), ex.mensagem
    assert isinstance(origem.kw["port"], int) and isinstance(origem.kw["timeout"], int)
    assert isinstance(origem.kw["login_timeout"], int)
    n = db.scalar(select(func.count()).select_from(RegistroBronze).where(RegistroBronze.sistema_origem == "SAUDE"))
    assert n > 0


def test_cemiterio_coluna_inexistente_registra_motivo_seguro(client, tokens, db, origem):
    origem.faltando = {"Titulares": "Endereco"}
    r = client.post("/api/conectores/cemiterio/amostra", headers=tokens["admin"])
    ex = _ex(db, r)
    assert ex.status == "falha"
    assert ex.erro_classe == "ProgrammingError" and ex.erro_numero == 207 and ex.erro_tabela == "Titulares"
    assert ex.erro_objeto == "Endereco"
    assert ex.erro_local and re.match(r"^[\w.]+:\d+ in \w+$", ex.erro_local)
    for t in (r.text, ex.mensagem or "", ex.erro_local or "", str(ex.erro_objeto)):
        assert HOST not in t and USER not in t and PWD not in t and "SINTETICO" not in t
    ult = next(b for b in client.get("/api/conectores", headers=tokens["admin"]).json()["bases"]
               if b["nome"] == "Cemiterio")["ultima_carga"]
    assert ult["erro"]["numero"] == 207 and ult["erro"]["tabela"] == "Titulares"
    assert "coluna" in ult["erro"]["motivo"].lower()


def test_origem_vazia_nunca_sem_novidades(client, tokens, db, origem):
    origem.vazio = True
    for cid in ("iss", "saude"):
        r = client.post(f"/api/conectores/{cid}/amostra", headers=tokens["admin"])
        ex = _ex(db, r)
        assert ex.status == "origem_vazia" and ex.lidos == 0, (cid, ex.status, ex.mensagem)
        assert "Nenhuma linha lida da origem" in ex.mensagem


def test_iss_grava_bronze_e_prata_no_indicador(client, tokens, db, origem):
    r = client.post("/api/conectores/iss/amostra", headers=tokens["admin"])
    ex = _ex(db, r)
    assert ex.lidos == 3, ex.mensagem
    iss = next(b for b in client.get("/api/conectores", headers=tokens["admin"]).json()["bases"] if b["nome"] == "ISS")
    assert iss["contagens"]["bronze"] >= 3
    assert iss["contagens"]["prata"] == db.scalar(select(func.count()).select_from(
        __import__("app.models.camadas", fromlist=["Municipe"]).Municipe))


def test_testar_conexao_admin(client, tokens, db, origem):
    assert client.post("/api/conectores/saude/diagnostico", headers=tokens["operador"]).status_code == 403
    r = client.post("/api/conectores/saude/diagnostico", headers=tokens["admin"])
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["banco"] == "Saude" and "estrutura" in d  # estrutura: ver test_zz_estrutura.py
    assert any("DB_NAME()" in s for s in origem.sqls)
    for t in (r.text,):
        assert HOST not in t and USER not in t and PWD not in t


def test_valor_json_tipos_do_driver():
    import uuid

    from app.services.diagnostico import valor_json
    assert valor_json(dt.datetime(2020, 1, 2, 3, 4, 5)) == "2020-01-02T03:04:05"
    assert valor_json(dt.date(2020, 1, 2)) == "2020-01-02"
    assert valor_json(Decimal("10")) == 10 and valor_json(Decimal("1.50")) == "1.50"
    assert valor_json(b"\x00\x0f") == "0x000f" and valor_json(None) is None
    assert valor_json(uuid.UUID(int=1)) == "00000000-0000-0000-0000-000000000001"


@pytest.mark.parametrize(("numero", "trecho"), [(207, "Coluna inexistente"), (208, "Tabela ou view inexistente"),
                                                (229, "permissão"), (4060, "Banco configurado"),
                                                (18456, "Login recusado"), (20009, "não respondeu")])
def test_motivo_por_numero(numero, trecho):
    from app.services.diagnostico import motivo
    assert trecho in motivo("ProgrammingError", numero, "Titulares", "Endereco")
