"""PP1–PP11: painel de progresso por fonte. Dados sintéticos ZZTESTE; só leitura do controle (nunca a origem)."""
import json
import time
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, text

from app.models.controle import Agendamento, CheckpointCarga, Execucao
from app.services import progresso as pg

SIS = "ZZTESTE"
F = pg.Fonte("zzteste", "ZZTESTE", (SIS,), (SIS,), None, None)
AGORA = datetime(2026, 10, 29, 18, 0, tzinfo=UTC)  # 15:00 BRT


@pytest.fixture
def limpo(db):
    def zera():
        db.execute(delete(CheckpointCarga).where(CheckpointCarga.sistema == SIS))
        db.execute(delete(Execucao).where(Execucao.sistema == SIS))
        db.execute(delete(Agendamento).where(Agendamento.conector == "zzteste"))
        db.commit()
    zera()
    yield db
    zera()


def _ex(db, **kw):
    base = dict(tipo="carga_completa_conector", sistema=SIS, status="em_andamento", usuario="zzteste@aion.local",
                iniciado_em=AGORA - timedelta(minutes=30), atualizado_em=AGORA - timedelta(minutes=1), lidos=0, lotes=0)
    base.update(kw)
    e = Execucao(**base)
    db.add(e)
    db.commit()
    return e


def _cp(db, tabela, posicao, total=None, concluida=False):
    db.add(CheckpointCarga(sistema=SIS, tabela=tabela, posicao=posicao, total_origem=total, concluida=concluida))
    db.commit()


def test_pp1_nunca_rodou(limpo):
    r = pg.fonte(limpo, F, AGORA)
    assert (r["status"], r["status_texto"]) == ("nunca_rodou", "Nunca rodou")


@pytest.mark.parametrize("st,esperado,texto", [("sucesso", "concluida", "Concluída"), ("falha", "falhou", "Falhou"),
                                               ("cancelada", "cancelada", "Cancelada"), ("pausada", "pausada", "Pausada")])
def test_pp1_status_finais(limpo, st, esperado, texto):
    _ex(limpo, status=st, finalizado_em=AGORA, mensagem="ZZTESTE texto livre com Traceback e senha=xyz")
    r = pg.fonte(limpo, F, AGORA)
    assert (r["status"], r["status_texto"]) == (esperado, texto)
    assert "Traceback" not in json.dumps(r, default=str) and "senha" not in json.dumps(r, default=str)  # PP7


def test_pp1_pausada_por_agenda_e_aguardando(limpo):
    f = pg.Fonte("zzteste", "ZZTESTE", (SIS,), (SIS,), None, "zzteste")
    limpo.add(Agendamento(conector="zzteste", horario="02:00", ativo=False, ordem=99))
    limpo.commit()
    r = pg.fonte(limpo, f, AGORA)
    assert r["status"] == "pausada" and r["motivo"] == "Agenda desligada"
    limpo.execute(text("UPDATE controle.agendamentos SET ativo=true WHERE conector='zzteste'"))
    limpo.commit()
    limpo.expire_all()
    r = pg.fonte(limpo, f, AGORA)
    if pg.get_settings().agenda_habilitada:
        assert r["status"] == "aguardando" and r["aguardando_em_brt"] == "02:00"


def test_pp7_sil_agenda_pausada_aguardando_reset(limpo):
    f = pg.Fonte("sil", "SIL", (SIS,), (SIS,), None, "zzteste")
    limpo.add(Agendamento(conector="zzteste", horario="05:30", ativo=False, ordem=99))
    limpo.commit()
    assert pg.fonte(limpo, f, AGORA)["motivo"] == "Agenda pausada aguardando reset"


def test_pp2_pp3_total_conhecido_por_contagem(limpo):
    _cp(limpo, "ZZ_A", 3000, total=10000)
    _cp(limpo, "ZZ_B", 0, total=0)
    _ex(limpo, lotes=3, lidos=3000)
    p = pg.fonte(limpo, F, AGORA)["progresso"]
    assert (p["lote"], p["lotes_total"], p["percentual"]) == (3, 10, 30)
    assert p["origem_total"] == "contagem gravada na amostra/carga"


def test_pp2_total_desconhecido_sem_percentual(limpo):
    _cp(limpo, "ZZ_A", 2000, total=None)
    _ex(limpo, lotes=2, lidos=2000)
    p = pg.fonte(limpo, F, AGORA)["progresso"]
    assert p["lote"] == 2 and p["lotes_total"] is None and p["percentual"] is None and p["origem_total"] is None


def test_pp3_estimado_pela_ultima_carga(limpo):
    _ex(limpo, status="sucesso", lidos=5000, finalizado_em=AGORA - timedelta(days=1))
    _ex(limpo, lotes=1, lidos=1000)
    p = pg.fonte(limpo, F, AGORA)["progresso"]
    assert p["lotes_total"] == 5 and p["percentual"] == 20 and p["origem_total"] == "estimado pela última carga"


def test_pp4_tabelas_inclusive_zero(limpo):
    _cp(limpo, "ZZ_A", 1500, total=2000)
    _cp(limpo, "ZZ_VAZIA", 0, total=0, concluida=True)
    _ex(limpo, lotes=2, lidos=1500)
    t = {x["tabela"]: x for x in pg.fonte(limpo, F, AGORA)["tabelas"]}
    assert t["ZZ_VAZIA"]["lidas"] == 0 and t["ZZ_VAZIA"]["estado"] == "concluida"
    assert t["ZZ_A"]["lidas"] == 1500 and t["ZZ_A"]["estado"] == "lendo"


def test_pp5_calculando_antes_do_3o_lote_e_previsao_depois(limpo):
    _cp(limpo, "ZZ_A", 2000, total=10000)
    e = _ex(limpo, lotes=2, lidos=2000)
    p = pg.fonte(limpo, F, AGORA)["progresso"]
    assert p["calculando"] and p["previsao_termino"] is None and p["decorrido_s"] == 1800
    limpo.execute(text("UPDATE controle.checkpoints_carga SET posicao=3000 WHERE sistema=:s"), {"s": SIS})
    limpo.execute(text("UPDATE controle.execucoes SET lotes=3 WHERE id=:i"), {"i": e.id})
    limpo.commit()
    limpo.expire_all()
    p = pg.fonte(limpo, F, AGORA)["progresso"]
    # média 600 s/lote x 7 lotes restantes = 70 min -> 16:10 BRT
    assert not p["calculando"] and p["previsao_termino_brt"] == "16:10"


def test_pp6_possivel_travada_sem_mudar_status(limpo):
    lim = pg.get_settings().ge_orfa_min
    _ex(limpo, atualizado_em=AGORA - timedelta(minutes=lim + 4))
    r = pg.fonte(limpo, F, AGORA)
    assert r["status"] == "rodando" and r["progresso"]["possivel_travada"] and r["progresso"]["sem_sinal_min"] == lim + 4
    limpo.execute(text("UPDATE controle.execucoes SET atualizado_em=:a WHERE sistema=:s"),
                  {"a": AGORA - timedelta(minutes=1), "s": SIS})
    limpo.commit()
    limpo.expire_all()
    assert not pg.fonte(limpo, F, AGORA)["progresso"]["possivel_travada"]


def test_pp10_sei_nao_liberada(limpo, monkeypatch):
    for k in list(__import__("os").environ):
        if k.startswith("SIGPMF_SRC_SEI_"):
            monkeypatch.delenv(k)
    assert "sei" in [x.id for x in pg.fontes()]
    f = pg.Fonte("sei", "SEI", (SIS,), (SIS,), None, None)  # isolado: outros testes deixam execuções do SEI
    r = pg.fonte(limpo, f, AGORA)
    assert r["status_texto"] == "Não liberada"
    assert r["falta"] == ["Token do SEI não configurado", "Reclassificação incremental pendente"]


def test_pp9_todos_os_perfis_e_sem_dado_pessoal(client, tokens, limpo):
    _ex(limpo, usuario="ZZTESTE Fulano 123.456.789-09", mensagem="ZZTESTE CPF 123.456.789-09")
    for perfil in ("admin", "admin_lgpd", "operador", "leitura"):
        r = client.get("/api/fontes/progresso", headers=tokens[perfil])
        assert r.status_code == 200
        corpo = r.text
        assert "123.456.789" not in corpo and "Fulano" not in corpo and "zzteste@aion.local" not in corpo
    assert client.get("/api/fontes/progresso").status_code == 401
    ids = [f["id"] for f in r.json()["fontes"]]
    assert {"educacao", "sil", "sei", "saude"} <= set(ids)
    assert r.json()["atualizar_a_cada_s"] == 30


def test_pp8_endpoint_leve_com_10_mil_execucoes(client, tokens, limpo):
    limpo.execute(text("""INSERT INTO controle.execucoes (tipo, sistema, status, iniciado_em, lidos, novos, atualizados,
                          ignorados, lotes, duplicados, pendentes)
                          SELECT 'carga_completa_conector', :s, 'sucesso', now() - (g || ' min')::interval, g, 0, 0, 0, 1, 0, 0
                          FROM generate_series(1, 10000) g"""), {"s": SIS})
    limpo.commit()
    client.get("/api/fontes/progresso", headers=tokens["leitura"])  # aquece
    tempos = []
    for _ in range(5):
        t0 = time.perf_counter()
        assert client.get("/api/fontes/progresso", headers=tokens["leitura"]).status_code == 200
        tempos.append(time.perf_counter() - t0)
    assert sorted(tempos)[2] < 0.2, tempos


def test_pp8_nao_toca_a_origem(client, tokens, monkeypatch):
    import app.connectors.sqlserver.connector as sq
    def proibido(*a, **k):
        raise AssertionError("o painel não pode abrir conexão com a origem")
    monkeypatch.setattr(sq.SqlServerConnector, "__enter__", proibido, raising=False)
    monkeypatch.setattr(sq.SqlServerConnector, "__init__", proibido)
    assert client.get("/api/fontes/progresso", headers=tokens["admin"]).status_code == 200
