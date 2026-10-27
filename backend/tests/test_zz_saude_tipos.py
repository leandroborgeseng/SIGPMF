"""Robustez do caminho real da Saúde (/amostra -> SqlServerConnector -> pymssql falso) com tipos reais do driver.
Só dados sintéticos."""
import datetime as dt
import re
import sys
import types
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.identity.cpf import digitos_verificadores
from app.models.camadas import RegistroBronze
from app.models.controle import Execucao

HOST, USER, PWD = "srv-sintetico.invalid", "usuario_sintetico", "SenhaSintetica#123"


def _cpf_int(i):
    b = f"{123456700 + i:09d}"
    return int(b + digitos_verificadores(b))


def _cns_def(i):
    from app.identity.cns import _definitivo_esperado
    return _definitivo_esperado(f"{12345678900 + i:011d}")


def _valor(col: str, i: int, modo: str):
    c = col.lower()
    if modo == "nulos":
        return None if c not in ("matricula", "id", "codigo", "codigo_cadastro", "codigo_domicilio_familia") else i + 1
    if c in ("matricula", "id", "codigo", "codigo_cadastro", "codigo_domicilio_familia", "nr", "cod_bairro", "cd_rua"):
        return Decimal(i + 1) if i % 2 else i + 1
    if c == "cpf":
        return [_cpf_int(i), str(_cpf_int(i)), None, Decimal(_cpf_int(i)), 0][i % 5]
    if c == "cartao_sus":
        v = _cns_def(i)
        return [int(v), v, None, Decimal(v), ""][i % 5]
    if c == "cep":
        return [14400000 + i, "14400-000", None, Decimal(14400000), b"14400000"][i % 5]
    if c in ("nome", "mae", "pai", "nome_paciente", "nome_mae", "nomesocial", "complemento"):
        return [b"JOS\xc9 SINT\xc9TICO", "MARIA SINTÉTICA", None, "  ", 12345][i % 5]
    if c in ("normalizado", "matricula_correta", "falecido", "ativo", "status_resolvido"):
        return [True, b"\x01", None, 0, False][i % 5]
    if c == "sexo":
        return ["M", "F", None, b"M", 1][i % 5]
    if any(k in c for k in ("dt", "data", "dataobito")):
        return [dt.datetime(1980, 5, 6, 0, 0), dt.date(1981, 1, 2), None, "2020-01-01 10:00:00",
                dt.datetime(2020, 1, 1, tzinfo=dt.UTC)][i % 5]
    if c == "uuid":
        import uuid
        return uuid.UUID(int=i)
    return [None, f"SINT {col}", b"bytes", 7, Decimal("1.5")][i % 5]


class Origem:
    def __init__(self, modo="misto", tupla=False, binario=False):
        self.modo, self.tupla, self.binario = modo, tupla, binario

    def connect(self, **kw):
        origem = self

        class Cur:
            def __init__(self, as_dict):
                self.as_dict = as_dict

            def execute(self, sql, params=None):
                self.cols = re.findall(r"\[(\w+)\]", sql.split(" FROM ")[0])

            def fetchmany(self, n):
                linhas = []
                for i in range(10):
                    d = {c: _valor(c, i, origem.modo) for c in self.cols}
                    if origem.binario and "Complemento" in d:
                        d["Complemento"] = bytes(range(256)) * 20  # 5 KB
                    linhas.append(tuple(d.values()) if (origem.tupla or not self.as_dict) else d)
                return linhas

        class Conn:
            def cursor(self, as_dict=False):
                return Cur(as_dict)

            def rollback(self):
                pass

            def close(self):
                pass
        return Conn()


@pytest.fixture
def usa_origem(monkeypatch):
    def _f(**kw):
        o = Origem(**kw)
        monkeypatch.setitem(sys.modules, "pymssql", types.SimpleNamespace(connect=o.connect))
        return o
    for k, v in {"SQLSERVER_HOST": HOST, "SQLSERVER_USER": USER, "SQLSERVER_PASSWORD": PWD, "SQLSERVER_PORT": "1433",
                 "SQLSERVER_TIMEOUT": "15", "AMOSTRA_LIMITE": "100", "SQLSERVER_DATABASE_SAUDE": "Saude"}.items():
        monkeypatch.setenv(k, v)
    from app.core.config import get_settings
    get_settings.cache_clear()
    yield _f
    get_settings.cache_clear()


def _roda(client, tokens, db):
    r = client.post("/api/conectores/saude/amostra", headers=tokens["admin"])
    assert r.status_code == 202, r.text
    db.expire_all()
    return db.get(Execucao, r.json()["id"])


@pytest.mark.parametrize("modo", ["misto", "nulos"])
def test_saude_tipos_reais(client, tokens, db, usa_origem, modo):
    usa_origem(modo=modo)
    ex = _roda(client, tokens, db)
    assert ex.status in ("sucesso", "sem_novidades"), (ex.mensagem, ex.erro_classe, ex.erro_tabela, ex.erro_local)


def test_saude_linhas_em_tupla(client, tokens, db, usa_origem):
    usa_origem(tupla=True)
    ex = _roda(client, tokens, db)
    assert ex.status in ("sucesso", "sem_novidades"), (ex.mensagem, ex.erro_classe, ex.erro_tabela, ex.erro_local)


def test_binario_grande_na_bronze_vira_tamanho_e_hash(client, tokens, db, usa_origem):
    usa_origem(binario=True)
    ex = _roda(client, tokens, db)
    assert ex.status in ("sucesso", "sem_novidades"), (ex.mensagem, ex.erro_classe, ex.erro_local)
    r = db.scalars(select(RegistroBronze).where(RegistroBronze.sistema_origem == "SAUDE",
                                                RegistroBronze.tabela == "pacientes",
                                                RegistroBronze.execucao_id == ex.id)).first()
    comp = r.dados["Complemento"]
    assert isinstance(comp, dict) and comp["bytes"] == 5120 and len(comp["sha256"]) == 64
    assert db.scalar(select(func.max(func.length(func.cast(RegistroBronze.dados, __import__("sqlalchemy").Text))))) < 20000


def test_normalizacoes_com_tipos_do_driver():
    from app.identity import cns, cpf
    from app.identity.correlacao import normaliza_nome
    assert cpf.normaliza(1234567890) == "01234567890"          # int perde o zero à esquerda
    assert cpf.normaliza(0) is None and cpf.normaliza(False) is None and cpf.normaliza(None) is None
    assert cns.normaliza(12345678901234) == "012345678901234"
    assert normaliza_nome(12345) == "12345" and normaliza_nome(b"JOS\xc9") == "JOSE" and normaliza_nome(None) == ""


def test_bytes_json():
    from app.services.diagnostico import BINARIO_MAX, bytes_json, linha_json
    assert bytes_json("JOSÉ".encode()) == "JOSÉ" and bytes_json(b"JOS\xc9") == "JOSÉ"
    assert bytes_json(b"\x00\x01") == "0x0001"
    g = bytes_json(b"\x00" * (BINARIO_MAX + 1))
    assert g["bytes"] == BINARIO_MAX + 1 and len(g["sha256"]) == 64
    assert linha_json((1, b"A"), ("Id", "Nome")) == {"Id": 1, "Nome": "A"}
    with pytest.raises(ValueError):
        linha_json((1, 2), ("Id",))


def test_nenhuma_politica_seleciona_coluna_binaria_conhecida():
    """Sem tipos no mapeamento (docs só têm nomes): barra nomes típicos de image/varbinary(max)."""
    from app.connectors.sqlserver.politicas import POLITICAS
    suspeita = re.compile(r"foto|imagem|image|assinatura|arquivo|anexo|blob|binario|pdf|digitaliz", re.I)
    for p in POLITICAS.values():
        for tab, cols in p.colunas.items():
            assert not [c for c in cols if suspeita.search(c)], (p.sistema, tab)


def test_rowversion_sempre_hex():
    from app.services.diagnostico import bytes_json, linha_json
    rv = b"ABCDEFGH"  # 8 bytes imprimíveis: ainda assim hex
    assert bytes_json(rv) == "0x4142434445464748"
    for col in ("RowVersion", "TIMESTAMP", "Versao_Registro", "dt_timestamp"):
        assert bytes_json(b"AB", col) == "0x4142"
    assert linha_json({"RowVer": b"\x00" * 8, "rowversion": b"XY", "Nome": b"JOSE"}) == {
        "RowVer": "0x0000000000000000", "rowversion": "0x5859", "Nome": "JOSE"}
