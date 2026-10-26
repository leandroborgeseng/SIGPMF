"""S1-S8 (Saúde) e CM1-CM11 (Cemitério) com a fixture sintética de fixture_saude_cemiterio.py."""
import re

import pytest
from sqlalchemy import create_engine, text

from app.connectors.sqlserver.connector import ConfigSqlServer, SqlServerConnector
from app.connectors.sqlserver.politicas import CEMITERIO, SAUDE, TabelaRecusada
from app.identity import cns
from app.ingestion.fixture_saude_cemiterio import ESPERADO
from tests.conftest import TEST_URL

TABELAS = {"bronze_registros": "bronze.registros", "silver_cidadaos": "silver.cidadaos",
           "silver_identificadores": "silver.identificadores", "silver_vinculos": "silver.vinculos",
           "silver_correlacoes_pendentes": "silver.correlacoes_pendentes", "silver_inconsistencias": "silver.inconsistencias"}
CLINICAS = ("Hipertenso", "Diabetico", "Cancer", "DoencaMental", "COR", "CAUSA_MORTIS", "CODIGO_OMS", "MEDICO_OBITO",
            "CRM_MEDICO", "Senha")


def _q(sql, **p):
    eng = create_engine(TEST_URL)
    with eng.begin() as c:
        r = c.execute(text(sql), p)
        return r.all() if r.returns_rows else []


def _contagens():
    return {k: _q(f"select count(*) from {t}")[0][0] for k, t in TABELAS.items()}


@pytest.fixture(scope="module")
def carga(client, tokens):
    for t in ("gold.cidadao_dados", "gold.cidadao_sistemas", "silver.enderecos", "silver.vinculos", "silver.identificadores",
              "silver.correlacoes_pendentes", "silver.inconsistencias", "silver.cidadaos", "bronze.registros"):
        _q(f"delete from {t}")
    body = {"conjunto": "saude+cemiterio"}
    r1 = client.post("/api/conectores/demo/executar", headers=tokens["operador"], json=body)
    assert r1.status_code == 200, r1.text
    c1 = _contagens()
    r2 = client.post("/api/conectores/demo/executar", headers=tokens["operador"], json=body)
    return r1.json(), r2.json(), c1, _contagens()


def test_contagens_iguais_ao_esperado(carga):
    _, _, c1, _ = carga
    esp = ESPERADO["saude+cemiterio"]
    assert c1 == {k: esp[k] for k in TABELAS}


def test_idempotente_sem_novidades(carga):
    _, r2, c1, c2 = carga
    assert c1 == c2 and r2["status"] == "sem_novidades" and r2["novos"] == 0


def test_s1_cm1_tabelas_recusadas_auditadas(carga):
    alvos = {a for (a,) in _q("select alvo from controle.auditoria where acao='tabela_recusada'")}
    assert {"SAUDE.Lab_UsuariosWeb", "SAUDE.PACIENTES20200106B", "CEMITERIO.OBITO01(CONGELADO)",
            "CEMITERIO.Novo_Cemiterio"} <= alvos
    assert _q("select count(*) from bronze.registros where tabela in ('Lab_UsuariosWeb','PACIENTES20200106B',"
              "'OBITO01(CONGELADO)','Novo_Cemiterio')")[0][0] == 0


@pytest.mark.parametrize("pol", [SAUDE, CEMITERIO])
def test_s2_cm2_colunas_clinicas_nunca_no_select(pol):
    con = SqlServerConnector(ConfigSqlServer(host="x", port=1433, database="x", user="x", password="x", timeout=5, amostra_limite=100))
    for tabela in pol.colunas:
        sql = con.montar_select_politica(pol, tabela, 100)
        for c in CLINICAS:
            assert not re.search(rf"\b{c}\b", sql, re.IGNORECASE), (tabela, c)
        assert "*" not in sql.replace("TOP", "")


def test_s2_cm2_colunas_clinicas_nunca_no_bronze(carga):
    for c in CLINICAS:
        assert _q("select count(*) from bronze.registros where dados ? :c", c=c)[0][0] == 0


def test_tabela_fora_da_lista_recusada():
    for nome in ("Lab_UsuariosWeb", "2APAGAR_Pacientes", "TabelaQualquer"):
        with pytest.raises(TabelaRecusada):
            SAUDE.autoriza(nome)


def test_amostra_padrao_1000_configuravel():
    from app.core.config import Settings
    assert Settings.model_fields["amostra_limite"].default == 1000


def test_cns_validacao_oficial():
    d = cns.gera_definitivo("12345678901")
    p = cns.gera_provisorio("70000000000001")
    assert cns.tipo(d) == "definitivo" and cns.tipo(p) == "provisorio"
    assert not cns.valido(d[:-1] + str((int(d[-1]) + 1) % 10))
    assert not cns.valido("1234") and cns.mascara(d).startswith("***")


def test_s5_cns_ambiguo_pendente(carga):
    assert _q("select count(*) from silver.correlacoes_pendentes where motivo='cns_ambiguo'")[0][0] == 2


def test_s7_esus_sempre_revisao(carga):
    assert _q("select count(*) from silver.correlacoes_pendentes where motivo='esus_inconsistencia'")[0][0] == 2


def test_cm5_terceiro_cpf_invalido_pendente(carga):
    assert _q("select count(*) from silver.correlacoes_pendentes where motivo='terceiro_cpf_invalido'")[0][0] == 1


def test_cm6_obito_nunca_associa_e_nao_preseleciona(carga):
    assert _q("select count(*) from silver.vinculos where sistema='CEMITERIO' and tabela like 'Obito%'")[0][0] == 0
    assert _q("select count(*) from silver.cidadaos where falecido")[0][0] == 0
    amb = _q("select candidatos, detalhe from silver.correlacoes_pendentes where motivo='obito_ambiguo'")
    assert len(amb) == 1 and len(amb[0][0]) == 2 and amb[0][1]["pre_selecionado"] is None


def test_cm8_dedupe_historico_vale_obitos(carga):
    assert _q("select count(*) from silver.inconsistencias where tipo='obito_divergente_historico'")[0][0] == 1


def test_cns_e_cpf_mascarados_na_api(carga, client, tokens):
    txt = client.get("/api/silver/pendencias", headers=tokens["admin"]).text + \
        client.get("/api/silver/cidadaos", headers=tokens["admin"]).text
    assert not re.search(r"\b\d{15}\b", txt) and not re.search(r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b", txt)


def test_cm7_confirmar_obito_so_admin_lgpd(carga, client, tokens):
    pid, cand = _q("select id, candidatos->>0 from silver.correlacoes_pendentes where motivo='obito_revisao'")[0]
    url = f"/api/silver/pendencias/{pid}/confirmar-obito"
    corpo = {"cidadao_id": cand, "motivo": "Certidão conferida no cartório"}
    for perfil in ("admin", "operador", "leitura"):
        assert client.post(url, json=corpo, headers=tokens[perfil]).status_code == 403
    assert client.post(url, json={"cidadao_id": cand}, headers=tokens["admin_lgpd"]).status_code == 400
    r = client.post(url, json=corpo, headers=tokens["admin_lgpd"])
    assert r.status_code == 200, r.text
    assert _q("select falecido from silver.cidadaos where cidadao_id=:c", c=cand)[0][0] is True
    assert _q("select count(*) from controle.auditoria where acao='confirmar_obito'")[0][0] == 1
    # sobrevive a nova carga (reconstrução da silver)
    client.post("/api/conectores/demo/executar", headers=tokens["operador"], json={"conjunto": "saude+cemiterio"})
    assert _q("select falecido from silver.cidadaos where cidadao_id=:c", c=cand)[0][0] is True
