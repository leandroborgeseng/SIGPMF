# Roda por último (zzzzzzzz): grava bronze SAUDE/CEMITERIO. Só dados sintéticos.
"""Carga incremental e prata/ouro só quando as entradas mudam (IN1–IN4)."""
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, update

from app.connectors.sqlserver.politicas import TabelaRecusada, politica
from app.core.sql_guard import garante_somente_leitura
from app.models.camadas import RegistroBronze
from app.models.controle import Agendamento, Execucao, Parametro
from app.services import agenda as ag
from app.services import camadas_estado
from app.services import carga_completa as ccomp
from app.services import carga_conector as cc
from app.silver import processamento
from tests.test_zzz_carga_completa import Origem
from tests.test_zzzzzzz_agenda import (  # noqa: F401 - fixture origem
    _amostras,
    _esperar,
    _leitores,
    _local,
    _zera_agenda,
    origem,
)


def _completa(db, conector, modo="completa"):
    ex = ccomp.iniciar(db, "admin@aion.local", conector, modo=modo)  # disparo síncrono (fixture)
    db.expire_all()
    return db.get(Execucao, ex.id)


def _contador_reconstrucao(monkeypatch):
    n = {"prata": 0}
    original = processamento.reconstruir

    def conta(db, *a, **kw):
        n["prata"] += 1
        return original(db, *a, **kw)
    monkeypatch.setattr(processamento, "reconstruir", conta)
    return n


# ---------------------------------------------------------------- SQL

def test_in2_pagina_incremental_parametrizada_e_pela_politica():
    o = Origem("SAUDE")
    pol = politica("SAUDE")
    desde = ("dt_ultimaatual", datetime(2026, 10, 1, 0, 0))
    sql, params = o.montar_pagina(pol, "pacientes", ("Matricula",), 50, (123,), desde=desde)
    assert sql == garante_somente_leitura(sql)
    assert "([dt_ultimaatual] >= %s)" in sql and params == (123, desde[1])
    sql0, p0 = o.montar_pagina(pol, "pacientes", ("Matricula",), 50, None, desde=desde)
    assert "WHERE" in sql0 and p0 == (desde[1],)
    with pytest.raises(TabelaRecusada):
        o.montar_pagina(pol, "pacientes", ("Matricula",), 50, None, desde=("ColunaForaDaPolitica", desde[1]))


def test_in2_modo_invalido_recusado(db):
    with pytest.raises(ValueError):
        ccomp.iniciar(db, "admin@aion.local", "saude", modo="qualquer")


# ---------------------------------------------------------------- IN1: assinatura das entradas

def test_in1_assinatura_muda_com_bronze_parametro_e_regras(db):
    a = camadas_estado.assinatura(db)
    assert camadas_estado.assinatura(db) == a  # estável
    db.add(Parametro(chave="teste_in1", valor="1", descricao="teste"))
    db.commit()
    b = camadas_estado.assinatura(db)
    assert b["parametros"] != a["parametros"] and b["bronze"] == a["bronze"]
    db.delete(db.get(Parametro, "teste_in1"))
    db.commit()
    assert camadas_estado.assinatura(db)["parametros"] == a["parametros"]
    assert len(camadas_estado.hash_regras()) == 64


def test_in1_completa_sem_mudanca_pula_prata_e_ouro_mas_reconcilia(db, origem, monkeypatch):  # noqa: F811
    _amostras(db)
    from sqlalchemy import delete

    from app.models.controle import CamadasMarca
    db.execute(delete(CamadasMarca))  # Parte 2: começa sem marca d'água (1ª vez = recomposição completa)
    db.commit()
    n = _contador_reconstrucao(monkeypatch)
    ex1 = _completa(db, "saude")
    assert ex1.status == "sucesso" and n["prata"] == 1 and "silver=" in ex1.mensagem
    ex2 = _completa(db, "saude")
    assert ex2.status == "sucesso" and n["prata"] == 1                 # não reconstruiu
    assert ccomp.MSG_CAMADAS_MANTIDAS in ex2.mensagem and "reconciliacao=" in ex2.mensagem
    # entrada mudou (linha nova na origem -> bronze nova): reconstrói
    pac = origem["SAUDE"].linhas("pacientes")[0]
    origem["SAUDE"].extras["pacientes"] = [{**pac, "Matricula": 888888, "dt_ultimaatual": "2031-01-01 00:00:00"}]
    ex3 = _completa(db, "saude")
    assert ex3.status == "sucesso" and n["prata"] == 2 and ex3.novos >= 1


def test_in1_retomar_camadas_segue_do_checkpoint(db, origem, monkeypatch):  # noqa: F811
    """PI8 (TL 30/10 11h56): Retomar camadas segue a fila/marca; sem trabalho pendente, não recompõe tudo."""
    _amostras(db)
    _completa(db, "cemiterio")
    n = _contador_reconstrucao(monkeypatch)
    ex = db.get(Execucao, _completa(db, "cemiterio").id)
    assert n["prata"] == 0 and ccomp.MSG_CAMADAS_MANTIDAS in ex.mensagem
    ccomp._camadas(db, ex, "cemiterio", "CEMITERIO", pode_pular=False)
    assert n["prata"] == 0  # nada mudou desde a última marca: incremental vazio
    ccomp._camadas(db, ex, "cemiterio", "CEMITERIO", pode_pular=False, recompor=True)
    assert n["prata"] == 1  # recomposição só a pedido (PI6)


# ---------------------------------------------------------------- IN2: incremental

def test_in2_incremental_le_so_desde_a_marca_dagua(db, origem, monkeypatch):  # noqa: F811
    _amostras(db)
    _completa(db, "saude")
    origem["SAUDE"].desdes.clear()
    total_pac = len(origem["SAUDE"].linhas("pacientes"))
    pac = origem["SAUDE"].linhas("pacientes")[0]
    origem["SAUDE"].extras["pacientes"] = [{**pac, "Matricula": 777777, "dt_ultimaatual": "2032-06-01 10:00:00"}]
    monkeypatch.setenv("CARGA_INCREMENTAL_MARGEM_H", "0")
    from app.core.config import get_settings
    get_settings.cache_clear()
    n = _contador_reconstrucao(monkeypatch)
    ex = _completa(db, "saude", modo="incremental")
    assert ex.status == "sucesso" and ex.modo == "incremental", ex.mensagem
    assert "Carga incremental" in ex.mensagem and n["prata"] == 1
    usados = dict(origem["SAUDE"].desdes)
    assert usados["pacientes"] is not None and usados["pacientes"][0] == "dt_ultimaatual"
    lidos_pac = sum(1 for t, d in origem["SAUDE"].desdes if t == "pacientes")
    assert lidos_pac >= 1
    bronze = db.scalar(select(func.count()).select_from(RegistroBronze).where(
        RegistroBronze.sistema_origem == "SAUDE", RegistroBronze.tabela == "pacientes", RegistroBronze.id_origem == "777777"))
    assert bronze == 1
    assert ex.lidos < total_pac + 50  # não releu a tabela inteira de novo junto com as outras
    # incremental sem nada novo: só relê a margem, nada grava, prata/ouro mantidas
    ex2 = _completa(db, "saude", modo="incremental")
    assert ex2.status == "sucesso" and ex2.novos == 0 and ccomp.MSG_CAMADAS_MANTIDAS in ex2.mensagem
    get_settings.cache_clear()


def test_in2_marca_dagua_aplica_a_margem(db, origem, monkeypatch):  # noqa: F811
    _amostras(db)
    _completa(db, "saude")
    maior = db.scalar(select(func.max(RegistroBronze.atualizado_origem_em)).where(
        RegistroBronze.sistema_origem == "SAUDE", RegistroBronze.tabela == "pacientes"))
    monkeypatch.setenv("CARGA_INCREMENTAL_MARGEM_H", "24")
    from app.core.config import get_settings
    get_settings.cache_clear()
    m = ccomp.marca_dagua(db, "SAUDE", "pacientes")
    assert m.tzinfo is None and m == maior.replace(tzinfo=None) - timedelta(hours=24)
    assert ccomp.marca_dagua(db, "SAUDE", "TabelaSemBronze") is None
    get_settings.cache_clear()


# ---------------------------------------------------------------- IN3: agenda escolhe o modo

def test_in3_modo_da_vez(monkeypatch):
    from app.core.config import get_settings
    monkeypatch.setenv("AGENDA_COMPLETA_DIAS", "7")  # comportamento antigo, ainda configurável
    get_settings.cache_clear()
    a = Agendamento(conector="saude", horario="01:00", ativo=True, ultima_completa_em=None)
    agora = datetime(2026, 10, 28, 5, tzinfo=UTC)
    assert ag.modo_da_vez(a, agora) == "completa"
    a.ultima_completa_em = agora - timedelta(days=2)
    assert ag.modo_da_vez(a, agora) == "incremental"
    a.ultima_completa_em = agora - timedelta(days=7)
    assert ag.modo_da_vez(a, agora) == "completa"
    get_settings.cache_clear()


def test_r3_agenda_padrao_0_primeira_completa_depois_incremental(db, origem, monkeypatch):  # noqa: F811
    """R3: com AGENDA_COMPLETA_DIAS=0 (padrão) a primeira execução agendada (ultima_completa_em nulo) é completa;
    a seguinte é incremental e nunca volta a ser completa."""
    from app.core.config import get_settings
    monkeypatch.delenv("AGENDA_COMPLETA_DIAS", raising=False)
    get_settings.cache_clear()
    _amostras(db)
    _zera_agenda(db)
    db.execute(update(Agendamento).values(ultima_completa_em=None))
    db.execute(update(Agendamento).where(Agendamento.conector == "cemiterio").values(ativo=False))
    db.commit()
    ag.ciclo(db, agora=_local(2026, 10, 28, 5, 0), leitores=_leitores(origem), esperar=_esperar)
    s = db.scalar(select(Agendamento).where(Agendamento.conector == "saude"))
    db.refresh(s)
    ex1 = db.get(Execucao, s.ultima_execucao_id)
    assert ex1.modo == "completa" and s.ultima_completa_em is not None, s.ultima_mensagem
    assert (ex1.lidos or 0) > 0
    assert ag.modo_da_vez(s, _local(2026, 11, 28, 5, 0)) == "incremental"
    _zera_agenda(db, ativo=False)
    get_settings.cache_clear()


def test_in3_agenda_faz_completa_e_depois_incremental(db, origem, monkeypatch):  # noqa: F811
    from app.core.config import get_settings
    monkeypatch.setenv("AGENDA_COMPLETA_DIAS", "7")  # modo antigo (completa periódica), ainda configurável
    get_settings.cache_clear()
    _amostras(db)
    _zera_agenda(db)
    db.execute(update(Agendamento).values(ultima_completa_em=None))
    db.execute(update(Agendamento).where(Agendamento.conector == "cemiterio").values(ativo=False))
    db.commit()
    ag.ciclo(db, agora=_local(2026, 10, 28, 5, 0), leitores=_leitores(origem), esperar=_esperar)
    s = db.scalar(select(Agendamento).where(Agendamento.conector == "saude"))
    db.refresh(s)
    ex1 = db.get(Execucao, s.ultima_execucao_id)
    assert ex1.modo == "completa" and s.ultima_completa_em is not None and "Carga completa" in s.ultima_mensagem
    pac = origem["SAUDE"].linhas("pacientes")[0]
    origem["SAUDE"].extras["pacientes"] = [{**pac, "Matricula": 666666, "dt_ultimaatual": "2033-01-01 00:00:00"}]
    feitos = ag.ciclo(db, agora=_local(2026, 10, 29, 5, 0), leitores=_leitores(origem), esperar=_esperar)
    assert feitos == [("saude", "sucesso")]
    db.refresh(s)
    ex2 = db.get(Execucao, s.ultima_execucao_id)
    assert ex2.modo == "incremental" and "Carga incremental" in s.ultima_mensagem
    item = next(i for i in ag.estado(db, agora=_local(2026, 10, 29, 6, 0))["itens"] if i["conector"] == "saude")
    assert item["proximo_modo"] == "incremental" and item["ultima_execucao"]["modo"] == "incremental"
    _zera_agenda(db, ativo=False)
    get_settings.cache_clear()
    _ = cc  # conector importado para a fixture
