"""Conector Munícipe (ISS.Municipes), aceite M1-M8. Só dados sintéticos gerados aqui (M8)."""
import sys
import types
from datetime import datetime

import pytest

from app.connectors.sqlserver.connector import ConfigSqlServer, SqlServerConnector
from app.connectors.sqlserver.politicas import ISS
from app.identity import documento as docmod
from app.identity.correlacao import cidadao_id_por_cpf
from app.identity.cpf import digitos_verificadores
from app.ingestion import municipe as mun


def cpf(n: int) -> str:
    base = f"{n:09d}"
    return base + digitos_verificadores(base)


def cnpj(n: int) -> str:
    base = f"{n:08d}0001"
    for dv in range(100):
        d = base + f"{dv:02d}"
        if docmod.cnpj_valido(d):
            return d
    raise AssertionError


def fmt_cpf(d: str) -> str:
    return f" {d[:3]}.{d[3:6]}.{d[6:9]}-{d[9:]} "


def fmt_cnpj(d: str) -> str:
    return f"{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:]}"


def linha(doc, tipo="F", nome="FULANO SINTETICO", quando="2026-01-01T10:00:00", mae="MAE SINTETICA"):
    return {"Tipo_Pessoa": tipo, "CPF": doc, "Nome": nome, "Nome_Mae": mae, "Data_Nascimento": "1980-05-06",
            "Pis_Pasep": None, "Data_Alteracao": quando}


A, B, C = cpf(123456781), cpf(234567892), cpf(345678903)
J = cnpj(12345678)


def conjunto():
    return [
        linha(fmt_cpf(A), nome="ANA V1", quando="2026-01-01T10:00:00"),
        linha(A, nome="ANA V2", quando="2026-03-01T10:00:00"),          # mais recente: vence
        linha(A[:3] + "-" + A[3:], nome="ANA V1", quando="2025-01-01"),  # igual à V1: não diverge de novo
        linha(B, quando=datetime(2026, 2, 2)),
        linha(fmt_cnpj(J), tipo="J", nome="EMPRESA SINTETICA LTDA"),
        linha(C, tipo="R", nome="TIPO R"),
        linha(cpf(456789014), tipo=None, nome="TIPO NULO"),
        linha(None, nome="SEM DOC"),
        linha("  ", nome="DOC VAZIO"),
        linha("..-/", nome="SO PONTUACAO"),
        linha(A[:9] + str((int(A[9]) + 1) % 10) + A[10], nome="DV INVALIDO"),
    ]


# ---------- M2 ----------
def test_m2_normaliza_so_digitos_e_vazio_nulo():
    assert docmod.normaliza(fmt_cpf(A)) == A
    assert docmod.normaliza(fmt_cnpj(J)) == J
    assert docmod.normaliza("  ") is None and docmod.normaliza("./-") is None and docmod.normaliza(None) is None


def test_m2_pontuacao_diferente_e_o_mesmo_documento():
    r = mun.classificar([linha(fmt_cpf(A)), linha(A, quando="2027-01-01")])
    assert len(r.registros) == 1 and r.contagem["duplicadas"] == 1


def test_m2_nulo_vazio_e_dv_invalido_vao_para_pendencia_sem_cidadao():
    r = mun.classificar(conjunto())
    motivos = sorted(p["motivo"] for p in r.pendencias)
    assert motivos.count("documento_nulo") == 3 and motivos.count("documento_invalido") == 1
    assert all("cidadao_id" not in p for p in r.pendencias)
    assert {p["nome"] for p in r.pendencias} >= {"SEM DOC", "DOC VAZIO", "SO PONTUACAO", "DV INVALIDO"}


# ---------- M1 ----------
def test_m1_um_por_documento_mais_recente_e_versoes_divergentes():
    r = mun.classificar(conjunto())
    reg_a = [x for x in r.registros if x.documento == A]
    assert len(reg_a) == 1 and reg_a[0].dados["nome"] == "ANA V2"
    assert r.contagem["duplicadas"] == 2
    vers = [v for v in r.versoes if v["documento"] == A]
    assert len(vers) == 1 and vers[0]["dados"]["nome"] == "ANA V1"


def test_m1_empate_de_data_vale_a_ultima_lida():
    r = mun.classificar([linha(A, nome="X", quando="2026-01-01"), linha(A, nome="Y", quando="2026-01-01")])
    assert r.registros[0].dados["nome"] == "Y"


# ---------- M3 / M4 / M5 ----------
def test_m3_juridica_vira_empresa_sem_cidadao():
    r = mun.classificar(conjunto())
    emp = [x for x in r.registros if x.documento == J]
    assert len(emp) == 1 and emp[0].tipo == "empresa" and emp[0].cidadao_id is None
    assert r.contagem["empresas"] == 1


def test_m4_tipo_r_ou_nulo_vai_para_pendencia():
    r = mun.classificar(conjunto())
    p = [x for x in r.pendencias if x["motivo"] == "tipo_pessoa_indefinido"]
    assert sorted(x["tipo_pessoa"] or "-" for x in p) == ["-", "R"]
    assert C not in {x.documento for x in r.registros}


def test_m5_fisica_cpf_valido_score_1():
    r = mun.classificar(conjunto())
    b = next(x for x in r.registros if x.documento == B)
    assert b.tipo == "cidadao" and b.confianca == 1.0 and b.cidadao_id == cidadao_id_por_cpf(B)


# ---------- M6 ----------
def test_m6_contagens_fecham():
    r = mun.classificar(conjunto())
    c = r.contagem
    assert c["lidas"] == 11 and r.fecha()
    assert (c["novas"], c["atualizadas"], c["inalteradas"], c["duplicadas"], c["pendentes"]) == (3, 0, 0, 2, 6)
    existentes = {x.documento: x.hash for x in r.registros}
    existentes[B] = "hash-antigo"
    r2 = mun.classificar(conjunto(), existentes)
    assert (r2.contagem["novas"], r2.contagem["atualizadas"], r2.contagem["inalteradas"]) == (0, 1, 2) and r2.fecha()


def test_m6_le_pelo_guard_com_top_limite_e_rollback(monkeypatch):
    log = []

    class Cur:
        def execute(self, sql, params):
            log.append(("execute", sql))

        def fetchmany(self, n):
            log.append(("fetchmany", n))
            return [linha(B)]

    class Conn:
        def cursor(self, as_dict=False):
            return Cur()

        def rollback(self):
            log.append(("rollback",))

        def close(self):
            log.append(("close",))
    monkeypatch.setitem(sys.modules, "pymssql", types.SimpleNamespace(connect=lambda **kw: Conn()))
    from app.services.carga_municipe import ler_origem
    con = SqlServerConnector(ConfigSqlServer("h", 1433, "ISS", "u", "p", 5, 1000), nome="ISS")
    assert ler_origem(con) == [linha(B)]
    sql = log[0][1]
    assert sql.startswith("SELECT TOP (1000) [Tipo_Pessoa]") and "FROM [dbo].[Municipes]" in sql and "*" not in sql
    assert log[1:] == [("fetchmany", 1000), ("rollback",), ("close",)]
    assert con.montar_select_politica(ISS, "Municipes", limite=5000).startswith("SELECT TOP (1000)")


# ---------- M7 ----------
def test_m7_mascara_cpf_e_cnpj():
    assert docmod.mascara(A) == f"***.{A[3:6]}.{A[6:9]}-**"
    m = docmod.mascara(J)
    assert J not in m and m.startswith("**.") and m.endswith("/****-**")


# ---------- Integração com banco de teste ----------
@pytest.fixture
def carga(client, db):
    from app.services.carga_municipe import executar_carga_municipe
    return executar_carga_municipe(db, "operador@aion.local", linhas=conjunto())


def test_carga_grava_silver_e_controle(carga, db):
    from app.models.camadas import Municipe, MunicipePendencia, MunicipeVersao
    assert carga.status in ("sucesso", "sem_novidades")
    assert carga.lidos == 11 and carga.duplicados == 2 and carga.pendentes == 6
    assert carga.lidos == carga.novos + carga.atualizados + carga.ignorados + carga.duplicados + carga.pendentes
    docs = {m.documento: m for m in db.query(Municipe)}
    assert A in docs and J in docs and docs[J].cidadao_id is None and C not in docs
    assert db.query(MunicipeVersao).filter_by(documento=A).count() == 1
    assert db.query(MunicipePendencia).count() >= 6
    for texto in (carga.mensagem or "", ):
        assert A not in texto and B not in texto and J not in texto


def test_carga_repetida_e_idempotente(carga, db):
    from app.services.carga_municipe import executar_carga_municipe
    ex = executar_carga_municipe(db, "operador@aion.local", linhas=conjunto())
    assert ex.novos == 0 and ex.atualizados == 0 and ex.ignorados == 3 and ex.status == "sem_novidades"


def test_m7_api_mascara_documento(carga, client, tokens):
    r = client.get("/api/silver/municipes", headers=tokens["leitura"])
    assert r.status_code == 200, r.text
    corpo = r.text
    for d in (A, B, J):
        assert d not in corpo
    tipos = {x["tipo"] for x in r.json()}
    assert tipos <= {"cidadao", "empresa"}
    emp = [x for x in r.json() if x["tipo"] == "empresa"]
    assert emp and all(x["cidadao_id"] is None for x in emp)
    p = client.get("/api/silver/municipes/pendencias", headers=tokens["operador"])
    assert p.status_code == 200 and A not in p.text


def test_execucao_municipe_rota_antiga_so_admin(client, tokens, monkeypatch):
    # PB13 (#8): a rota antiga delega à amostra por conector, só admin
    r = client.post("/api/conectores/iss/municipes/executar", headers=tokens["operador"])
    assert r.status_code == 403


# ---------- Ajustes do TL no #6: Documento_SoNumeros e ordenação da amostra ----------
def test_documento_sonumeros_na_politica_e_ordem_da_amostra():
    from app.connectors.sqlserver.politicas import ORDEM_MUNICIPES
    from app.core.sql_guard import garante_somente_leitura
    assert "Documento_SoNumeros" in ISS.colunas_select("Municipes")
    assert ORDEM_MUNICIPES == ("Tipo_Pessoa", "Documento_SoNumeros")
    con = SqlServerConnector(ConfigSqlServer("h", 1433, "ISS", "u", "p", 5, 1000), nome="ISS")
    sql = con.montar_select_politica(ISS, "Municipes")
    assert sql.startswith("SELECT TOP (1000) ") and "[Documento_SoNumeros]" in sql and "*" not in sql
    assert sql.endswith("FROM [dbo].[Municipes] ORDER BY [Tipo_Pessoa], [Documento_SoNumeros]")
    assert "Data_Alteracao] DESC" not in sql
    assert garante_somente_leitura(sql) == sql  # ORDER BY continua aceito pelo guard
    assert con.montar_select_politica(ISS, "Municipes", limite=5000).startswith("SELECT TOP (1000)")


def test_ordem_fora_da_politica_recusada():
    from dataclasses import replace

    from app.connectors.sqlserver.politicas import TabelaRecusada
    ruim = replace(ISS, ordem={"Municipes": ("Senha_Qualquer",)})
    con = SqlServerConnector(ConfigSqlServer("h", 1433, "ISS", "u", "p", 5, 1000), nome="ISS")
    with pytest.raises(TabelaRecusada):
        con.montar_select_politica(ruim, "Municipes")


def test_documento_sonumeros_confere_normalizacao():
    ok = {**linha(f"{A[:3]}.{A[3:6]}.{A[6:9]}-{A[9:]}"), "Documento_SoNumeros": A}
    diverge = {**linha(B), "Documento_SoNumeros": "00000000000"}
    sem = linha(J, tipo="J")  # sem a coluna: não confere nem conta divergência
    r = mun.classificar([ok, diverge, sem])
    assert r.contagem["divergencia_documento"] == 1
    assert r.fecha()
    docs = {reg.documento for reg in r.registros}
    assert A in docs and B in docs  # vale o normalizado do CPF mesmo com divergência
    assert not mun.diverge_documento(ok) and mun.diverge_documento(diverge) and not mun.diverge_documento(sem)


# ---------- Ressalvas baixas do QA do #6 (R1-R4) ----------
def test_m9_tipo_divergente_manda_todas_para_pendencia():
    f = linha(A, nome="PESSOA F", quando="2026-01-01T10:00:00")
    r_ = linha(A, tipo="R", nome="PESSOA R", quando="2026-02-01T10:00:00")
    n_ = linha(B, tipo=None, nome="NULA B")
    fb = linha(B, nome="F B")
    f2 = linha(C, nome="C1", quando="2026-01-01T10:00:00")
    f3 = linha(C, nome="C2", quando="2026-03-01T10:00:00")
    r = mun.classificar([f, r_, n_, fb, f2, f3])
    assert r.fecha()
    div = [p for p in r.pendencias if p["motivo"] == "tipo_divergente"]
    assert {p["nome"] for p in div} == {"PESSOA F", "PESSOA R", "NULA B", "F B"}
    assert [g.dados["nome"] for g in r.registros] == ["C2"] and r.contagem["duplicadas"] == 1


@pytest.mark.parametrize("col", ["Num_CTPS", "Titulo", "Senha_Web", "titulo_eleitor", "CNH_Numero", "remember_token",
                                 "Password", "CID", "Cod_CID", "CID10"])
def test_r2_coluna_sensivel_por_padrao(col):
    from dataclasses import replace

    from app.connectors.sqlserver.politicas import TabelaRecusada
    p = replace(ISS, colunas={"Municipes": ISS.colunas["Municipes"] + (col,)})
    with pytest.raises(TabelaRecusada):
        p.colunas_select("Municipes")


@pytest.mark.parametrize("col", ["Cidade", "Cidadao_Id", "Nome_Mae"])
def test_r2_padrao_nao_pega_falsos_positivos(col):
    from dataclasses import replace
    p = replace(ISS, colunas={"Municipes": ISS.colunas["Municipes"] + (col,)})
    assert col in p.colunas_select("Municipes")


def test_r3_chave_de_pendencia_nao_depende_da_posicao():
    a, b = linha(None, nome="SEM DOC 1"), linha(C, tipo="R", nome="TIPO R")
    k1 = sorted(p["chave"] for p in mun.classificar([a, b]).pendencias)
    k2 = sorted(p["chave"] for p in mun.classificar([linha(A), b, a]).pendencias)
    assert k1 == k2


def test_m10_pendencias_403_para_leitura(carga, client, tokens):
    assert client.get("/api/silver/municipes/pendencias").status_code == 401
    assert client.get("/api/silver/municipes/pendencias", headers=tokens["leitura"]).status_code == 403
    for perfil in ("operador", "admin"):
        p = client.get("/api/silver/municipes/pendencias", headers=tokens[perfil])
        assert p.status_code == 200 and p.json()
        assert A not in p.text and B not in p.text and C not in p.text and J not in p.text
        mascarados = [x["documento"] for x in p.json() if x["documento"]]
        assert mascarados and all("*" in d for d in mascarados)
