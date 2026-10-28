# Roda por último: grava bronze SAUDE (fatos) e tributária. Só dados sintéticos.
"""Onda 3 — fatos da Saúde sem conteúdo clínico, linha do tempo e ouro agregado (SA-F1..SA-F6)."""
import re
from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.connectors.sqlserver.politicas import politica
from app.core import recursos
from app.core.config import get_settings
from app.ingestion.origem_mock import OrigemMock
from app.models.camadas import Agendamento, Atendimento, Evento, Indicador, RegistroBronze, Vacinacao
from app.models.controle import Execucao
from app.services import carga_completa as ccomp
from app.services import carga_conector as cc
from tests.test_zz_carga_conector import Leitor
from tests.test_zzz_carga_completa import Origem
from tests.test_zzzzzzzzz_tributario import _cid_sa1

TUDO = "saude+cemiterio+tributario+saude_fatos"
CLINICAS = {"Queixa_principal", "HMA", "Suspeita", "Exame_Fisico", "PA", "Peso", "MotivoExclusao", "Lote", "Observacao"}


class OrigemTudo(Origem):
    def __init__(self, sistema):
        super().__init__(sistema)
        self.mock = OrigemMock(conjunto=TUDO)


class LeitorTudo(Leitor):
    def __init__(self, sistema):
        super().__init__(sistema)
        self.o = OrigemMock(conjunto=TUDO)


@pytest.fixture
def carregado(db, monkeypatch):
    o = {s: OrigemTudo(s) for s in ("SAUDE", "CEMITERIO", "IPTU", "DIVIDA", "FINANCEIRO")}
    monkeypatch.setenv("CARGA_COMPLETA_LOTE", "50")
    monkeypatch.setenv("CARGA_COMPLETA_PAUSA_MS", "0")
    monkeypatch.setattr(recursos, "disco", lambda: recursos.Uso(total=100, livre=50))
    get_settings.cache_clear()
    monkeypatch.setattr(ccomp, "leitor_padrao", lambda conector, db: o[cc.CONECTORES[conector][0]])

    def sincrono(ex_id, conector, trava, somente_camadas=False):
        ccomp.rodar(ex_id, conector, trava, leitor=o[cc.CONECTORES[conector][0]], somente_camadas=somente_camadas)
    monkeypatch.setattr(ccomp, "disparar", sincrono)
    for conector in ("saude", "iptu", "financeiro", "divida"):
        a = cc.executar_amostra(db, "admin@aion.local", conector, leitor=LeitorTudo(cc.CONECTORES[conector][0]))
        assert a.status in ("sucesso", "sem_novidades"), a.mensagem
        ex = ccomp.iniciar(db, "admin@aion.local", conector)
        db.expire_all()
        assert db.get(Execucao, ex.id).status == "sucesso", db.get(Execucao, ex.id).mensagem
    yield _cid_sa1(db)
    get_settings.cache_clear()


def test_saf1_politica_sem_clinico_e_com_recorte_por_data():
    pol = politica("SAUDE")
    for t in ("Consultas", "Agendamento_Consulta", "Vacina"):
        assert not CLINICAS & set(pol.colunas_select(t))
    o = Origem("SAUDE")
    corte = (date.today() - timedelta(days=365)).isoformat()
    assert f"([Dt_Atend] >= '{corte}')" in o.montar_contagem(pol, "Consultas")
    assert "WHERE" not in o.montar_contagem(pol, "pacientes")


def test_saf2_fatos_ligados_pela_matricula_e_sem_clinico_na_bronze(db, carregado):
    cid = carregado
    for d in db.scalars(select(RegistroBronze.dados).where(RegistroBronze.sistema_origem == "SAUDE",
                                                          RegistroBronze.tabela.in_(("Consultas", "Agendamento_Consulta", "Vacina")))):
        assert not CLINICAS & set(d)
    at = list(db.scalars(select(Atendimento).where(Atendimento.chave_origem.in_(("1001|1", "1001|2", "424242|1")))))
    assert sorted(a.cidadao_id == cid for a in at) == [False, True, True]  # paciente sem vínculo fica só no agregado
    ag = db.scalar(select(Agendamento).where(Agendamento.matricula == "1001"))
    assert ag.cidadao_id == cid and ag.dias_espera == 30
    assert db.scalar(select(Vacinacao).where(Vacinacao.matricula == "1001")).vacina == "Influenza"


def test_saf3_linha_do_tempo_de_todas_as_areas(client, tokens, db, carregado):
    cid = carregado
    tipos = {e.tipo for e in db.scalars(select(Evento).where(Evento.cidadao_id == cid))}
    assert {"consulta", "agendamento", "vacina", "posse", "pagamento", "divida"} <= tipos
    r = client.get(f"/api/gold/cidadaos/{cid}/linha-do-tempo?limite=3", headers=tokens["leitura"])
    assert r.status_code == 200
    evs = r.json()["eventos"]
    assert len(evs) == 3 and evs[0]["data"] >= evs[1]["data"] >= evs[2]["data"]  # mais novo primeiro
    r2 = client.get(f"/api/gold/cidadaos/{cid}/linha-do-tempo?antes={evs[-1]['data']}", headers=tokens["leitura"]).json()
    assert all(e["data"] < evs[-1]["data"] for e in r2["eventos"])
    assert client.get(f"/api/gold/cidadaos/{cid}/linha-do-tempo?antes=ontem", headers=tokens["leitura"]).status_code == 422
    assert all("clinico" not in e["titulo"] for e in evs)


def test_saf5_saude_do_cidadao(client, tokens, carregado):
    d = client.get(f"/api/gold/cidadaos/{carregado}/saude", headers=tokens["leitura"]).json()
    assert d["proximos"][0]["especialidade"] == "Oftalmologia" and d["proximos"][0]["dias_espera"] == 30
    assert d["vacinas"][0]["vacina"] == "Influenza" and len(d["atendimentos"]) >= 2


def test_saf4_indicadores_sem_pessoa(client, tokens, db, carregado):
    r = client.get("/api/indicadores", headers=tokens["leitura"])
    assert r.status_code == 200
    s = r.json()["series"]
    kpi = {x["dim"]: x["valor"] for x in s["kpi"]}
    from sqlalchemy import func

    from app.models.camadas import DividaAtiva
    assert kpi["atendimentos_12m"] == db.scalar(select(func.count()).select_from(Atendimento)) >= 3
    assert kpi["vacinas_24m"] == db.scalar(select(func.count()).select_from(Vacinacao)) >= 1
    assert kpi["divida_ativa"] == pytest.approx(float(db.scalar(select(func.sum(DividaAtiva.saldo))
                                                                .where(DividaAtiva.extinta.is_(False)))))
    assert kpi["devedores"] >= 1 and kpi["protestadas"] >= 1 and kpi["espera_media_dias"] > 0
    assert sum(x["valor"] for x in s["saude_atend_mes"]) <= kpi["atendimentos_12m"]  # só os com data
    assert sum(x["valor"] for x in s["trib_devedores_faixa"]) == kpi["devedores"]
    assert all(x["valor"] >= 5 for x in s.get("saude_atend_unidade", []))  # grupos com menos de 5 casos: suprimidos
    # nenhum identificador de pessoa no ouro agregado
    for i in db.scalars(select(Indicador)):
        assert carregado not in i.dim and not re.search(r"\d{11,}", i.dim)  # nem cidadão nem CPF/CNPJ
    so = client.get("/api/indicadores?series=kpi", headers=tokens["leitura"]).json()["series"]
    assert list(so) == ["kpi"]
    assert client.get("/api/indicadores").status_code == 401
