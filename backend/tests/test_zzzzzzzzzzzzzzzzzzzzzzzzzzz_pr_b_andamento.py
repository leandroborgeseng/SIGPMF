"""PR-B andamento das cargas (CT5–CT12, CT16–CT22). Dados sintéticos ZZTESTE; banco *_test; nada toca a origem."""
import json
import threading
import time
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, delete, inspect, select, text

from app.models.controle import Execucao, ExecucaoSubetapa, Sistema
from app.services import andamento
from app.services import subetapa as sub

SIS = "ZZTESTE_PRB"
AGORA = datetime.now(UTC)


@pytest.fixture
def limpo(db):
    def zera():
        ids = select(Execucao.id).where(Execucao.sistema == SIS)
        db.execute(delete(ExecucaoSubetapa).where(ExecucaoSubetapa.execucao_id.in_(ids)))
        db.execute(delete(Execucao).where(Execucao.sistema == SIS))
        db.commit()
    if not db.scalar(select(Sistema).where(Sistema.codigo == SIS)):
        db.add(Sistema(codigo=SIS, nome="ZZTESTE Fonte PR-B", dominio="teste"))
        db.commit()
    zera()
    yield db
    zera()


def _ex(db, **kw):
    base = dict(tipo="carga_completa_conector", sistema=SIS, status="em_andamento", usuario="zzteste@aion.local",
                iniciado_em=AGORA - timedelta(minutes=30), lidos=0, lotes=0, novos=0, atualizados=0)
    base.update(kw)
    e = Execucao(**base)
    db.add(e)
    db.commit()
    return e


def _carga(r):
    return next(c for c in r["cargas"] if c["sistema"] == SIS)


def test_migracao_0035_colunas_e_idempotente(banco):
    from alembic import command

    from tests.conftest import alembic_cfg
    eng = create_engine(banco)
    cols = {c["name"] for c in inspect(eng).get_columns("execucoes", schema="controle")}
    assert {"etapa", "subetapa", "subetapa_contador", "subetapa_total", "ultimo_sinal_em"} <= cols
    assert inspect(eng).has_table("execucao_subetapas", schema="controle")
    # idempotente: com as colunas já criadas, rodar a 0035 de novo não falha
    command.stamp(alembic_cfg(), "0033")
    command.upgrade(alembic_cfg(), "head")
    with eng.connect() as c:
        assert c.execute(text("select version_num from controle.alembic_version")).scalar() == "0035"
    eng.dispose()


def test_texto_seguro_recusa_dado_pessoal():
    assert sub.texto_seguro("Prata: buscando duplicados por CPF") == "Prata: buscando duplicados por CPF"
    for ruim in ("Prata: CPF 123.456.789-09", "lendo 12345678909", "zzteste@aion.local", "CNS 700000000000005"):
        with pytest.raises(sub.TextoInseguro):
            sub.texto_seguro(ruim)


def test_ct16_marca_subetapa_log_e_contador(limpo):
    e = _ex(limpo)
    sub.marca(e.id, "prata", "Prata: buscando duplicados por CPF", 0, 6_000_000)
    sub.marca(e.id, "prata", "Prata: buscando duplicados por CPF", 1_200_000, 6_000_000)  # mesmo texto: só contador
    sub.marca(e.id, "ouro", sub.TEXTO_OURO, 0, None)
    limpo.expire_all()
    ex = limpo.get(Execucao, e.id)
    assert (ex.etapa, ex.subetapa, ex.subetapa_total) == ("ouro", sub.TEXTO_OURO, None)
    assert ex.ultimo_sinal_em is not None
    log = list(limpo.scalars(select(ExecucaoSubetapa).where(ExecucaoSubetapa.execucao_id == e.id)
                             .order_by(ExecucaoSubetapa.id)))
    assert [s.subetapa for s in log] == ["Prata: buscando duplicados por CPF", sub.TEXTO_OURO]
    assert log[0].contador == 1_200_000 and log[0].finalizada_em is not None and log[1].finalizada_em is None


def test_ct16_pulso_mantem_sinal_durante_consulta_longa(limpo):
    """Consulta longa (pg_sleep) sem contagem parcial: o pulso continua gravando o sinal em outra conexão."""
    e = _ex(limpo)
    sub.marca(e.id, "prata", "Prata: buscando duplicados por CPF")
    limpo.expire_all()
    antes = limpo.get(Execucao, e.id).ultimo_sinal_em
    with sub.Pulso(e.id, intervalo=0.2):
        limpo.execute(text("select pg_sleep(1.0)"))
    limpo.commit()
    limpo.expire_all()
    assert limpo.get(Execucao, e.id).ultimo_sinal_em > antes


def test_ct16_medicao_abre_subetapas_da_prata_em_ordem(limpo):
    e = _ex(limpo)
    med = sub.MedicaoComSinal(e.id)
    med.marca("limpa_prata")
    med.marca("le_bronze_canonico", saida=10)
    med.marca("indice_cns")
    med.progresso(5000, 20000)
    limpo.expire_all()
    ex = limpo.get(Execucao, e.id)
    assert ex.subetapa == "Prata: buscando duplicados por CPF"
    assert (ex.subetapa_contador, ex.subetapa_total, ex.processadas_etapa) == (5000, 20000, 5000)
    n = limpo.scalar(select(text("count(*)")).select_from(ExecucaoSubetapa)
                     .where(ExecucaoSubetapa.execucao_id == e.id))
    assert n == 4


def test_endpoint_exige_login(client):
    assert client.get("/api/cargas/andamento").status_code in (401, 403)


def test_ct22_subetapa_longa_sem_linhas(limpo, client, tokens):
    """CT22: subetapa longa, 0 gravados, sinal recente -> rodando, sem_sinal_s pequeno; > 5 min -> sem sinal."""
    e = _ex(limpo, mensagem="ZZTESTE Traceback senha=xyz 123.456.789-09")
    sub.marca(e.id, "prata", "Prata: buscando duplicados por CPF")
    r = client.get("/api/cargas/andamento", headers=tokens["leitura"])
    assert r.status_code == 200
    c = _carga(r.json())
    a = c["atual"]
    assert (a["status"], a["etapa"], a["gravados"], a["subetapa_total"]) == ("rodando", "prata", 0, None)
    assert a["sem_sinal_s"] is not None and a["sem_sinal_s"] < 60
    assert r.json()["sem_sinal_limite_s"] == 300
    assert c["log"][0]["subetapa"] == "Prata: buscando duplicados por CPF"
    corpo = json.dumps(r.json())
    assert "Traceback" not in corpo and "senha" not in corpo and "123.456" not in corpo  # CT12/CT19
    # sinal velho: sem_sinal_s > 300
    limpo.execute(text("update controle.execucoes set ultimo_sinal_em = now() - interval '7 minutes' where id=:i"),
                  {"i": e.id})
    limpo.commit()
    a = _carga(client.get("/api/cargas/andamento", headers=tokens["leitura"]).json())["atual"]
    assert a["sem_sinal_s"] > 300 and a["status"] == "rodando"


def test_ct10_ct11_historico_30_parcial_e_erro(limpo):
    for i in range(35):
        _ex(limpo, status="sucesso", modo="incremental" if i % 2 else None,
            iniciado_em=AGORA - timedelta(hours=40 - i), finalizado_em=AGORA - timedelta(hours=39 - i))
    _ex(limpo, status="parcial", finalizado_em=AGORA)
    c = _carga(andamento.tela(limpo))
    assert len(c["historico"]) == 30
    assert c["atual"]["status"] == "parcial" and c["atual"]["proxima_acao"] == "Continua na próxima agenda"
    assert c["ultima_completa_em"] and c["ultima_incremental_em"]
    _ex(limpo, status="falha", finalizado_em=AGORA)
    a = _carga(andamento.tela(limpo))["atual"]
    assert (a["status"], a["proxima_acao"]) == ("erro", "Rodar de novo") and a["motivo"]


def test_endpoint_nao_toca_origem(limpo, monkeypatch):
    """Nenhum conector é aberto pelo endpoint (só Postgres)."""
    from app.connectors.sqlserver import connector as sqc
    chamado = threading.Event()
    monkeypatch.setattr(sqc.SqlServerConnector, "__init__", lambda *a, **k: chamado.set())
    _ex(limpo)
    t0 = time.monotonic()
    andamento.tela(limpo)
    assert not chamado.is_set() and time.monotonic() - t0 < 5
