"""QA #41 (Tester) — só dados sintéticos ZZTESTE, banco local de teste."""

import json
import statistics
import time
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, event, select, text

from app.models.camadas import RegistroBronze
from app.models.controle import Agendamento, CheckpointCarga, Execucao
from app.services import progresso as pg

SIS = "ZZTESTE"
AG = "zzteste"
AGORA = datetime(2026, 10, 29, 18, 0, tzinfo=UTC)
F = pg.Fonte("zzteste", "ZZTESTE", (SIS,), (SIS,), None, AG)
PII = "CPF 529.982.247-25 Fulano ZZTESTE da Silva senha=ZZsegredo123 Traceback (most recent call last) pyodbc.Error"


@pytest.fixture
def L(db):
    def z():
        zz = ("ZZTESTE", "ZZTESTE2")
        ids = select(Execucao.id).where(Execucao.sistema.in_(zz))
        db.execute(delete(CheckpointCarga).where(CheckpointCarga.sistema.in_(zz)))
        db.execute(delete(RegistroBronze).where(RegistroBronze.execucao_id.in_(ids)))
        db.execute(delete(Execucao).where(Execucao.sistema.in_(zz)))
        db.execute(delete(Agendamento).where(Agendamento.conector == AG))
        db.commit()

    z()
    yield db
    z()


def ex(db, **kw):
    b = dict(
        tipo="carga_completa_conector",
        sistema=SIS,
        status="em_andamento",
        usuario="zzteste@aion.local",
        iniciado_em=AGORA - timedelta(minutes=30),
        atualizado_em=AGORA - timedelta(minutes=1),
        lidos=0,
        lotes=0,
    )
    b.update(kw)
    e = Execucao(**b)
    db.add(e)
    db.commit()
    return e


def cp(db, t, pos, total=None, conc=False):
    db.add(CheckpointCarga(sistema=SIS, tabela=t, posicao=pos, total_origem=total, concluida=conc))
    db.commit()


class S:
    def __init__(s, base, **kw):
        s.b, s.kw = base, kw

    def __getattr__(s, k):
        return s.kw.get(k, getattr(s.b, k))


def cfg(mp, **kw):
    base = pg.get_settings()
    mp.setattr(pg, "get_settings", lambda: S(base, **kw))


# ---- acesso
def test_auth(client, tokens):
    assert client.get("/api/fontes/progresso").status_code == 401
    assert client.get("/api/fontes/progresso", headers={"Authorization": "Bearer zz.invalido"}).status_code == 401
    for p in ("admin", "admin_lgpd", "operador", "leitura"):
        r = client.get("/api/fontes/progresso", headers=tokens[p])
        assert r.status_code == 200, p
        assert "fontes" in r.json()


# ---- só leitura, sem origem
def test_somente_leitura(client, tokens, L, monkeypatch):
    ex(L, lidos=500, lotes=1)
    cp(L, "ZZ_A", 500)
    from sqlalchemy.engine import Engine as engine

    def tup():
        time.sleep(1.5)
        L.execute(text("select pg_stat_clear_snapshot()"))
        return L.execute(text("select coalesce(sum(n_tup_ins+n_tup_upd+n_tup_del),0) from pg_stat_user_tables")).scalar()

    L.commit()
    # Isolamento na suíte completa: conexões ociosas de testes anteriores ainda têm estatísticas pendentes, que o
    # Postgres só publica mais tarde (ou ao encerrar a conexão). Encerrá-las antes da 1ª leitura evita contar essas
    # escritas antigas como se fossem do endpoint. Não muda nenhum assert.
    from app.core.db import get_engine

    L.execute(text("select pg_stat_force_next_flush()"))
    L.commit()
    get_engine().dispose()
    t0 = tup()
    L.commit()
    stm = []

    def h(conn, cur, st, *a):
        return stm.append(st)

    event.listen(engine, "before_cursor_execute", h)
    import socket

    real = socket.create_connection

    def bloq(addr, *a, **k):
        if addr[0] not in ("127.0.0.1", "localhost"):
            raise AssertionError(f"origem {addr}")
        return real(addr, *a, **k)

    monkeypatch.setattr(socket, "create_connection", bloq)
    try:
        import pyodbc

        monkeypatch.setattr(pyodbc, "connect", lambda *a, **k: (_ for _ in ()).throw(AssertionError("SQL Server")))
    except ImportError:
        pass
    try:
        r = client.get("/api/fontes/progresso", headers=tokens["leitura"])
        assert r.status_code == 200
    finally:
        event.remove(engine, "before_cursor_execute", h)
    L.commit()
    t1 = tup()
    print("PGSTAT delta escritas", t1 - t0)
    escr = [s for s in stm if s.lstrip().split()[0].upper() not in ("SELECT", "WITH")]
    print("STATEMENTS", len(stm), "NAO-SELECT", escr)
    assert stm and not escr and t1 == t0


# ---- PP1
@pytest.mark.parametrize(
    "st,esp",
    [
        ("sucesso", "Concluída"),
        ("falha", "Falhou"),
        ("cancelada", "Cancelada"),
        ("pausada", "Pausada"),
        ("interrompida", "Pausada"),
    ],
)
def test_pp1_finais(L, st, esp):
    ex(L, status=st, finalizado_em=AGORA)
    assert pg.fonte(L, pg.Fonte("zzteste", "Z", (SIS,), (SIS,), None, None), AGORA)["status_texto"] == esp


def test_pp1_rodando_nunca(L):
    assert pg.fonte(L, F, AGORA)["status_texto"] == "Nunca rodou"
    ex(L)
    assert pg.fonte(L, F, AGORA)["status_texto"] == "Rodando"


def test_pp1_aguardando_com_hora(L, monkeypatch):
    cfg(monkeypatch, agenda_habilitada=True, agenda_janela="00:00-06:00")
    L.add(Agendamento(conector=AG, horario="02:30", ativo=True, ordem=99))
    L.commit()
    r = pg.fonte(L, F, AGORA)
    assert r["status_texto"] == "Aguardando" and r["aguardando_em_brt"] == "02:30", r


def test_pp1_pausada_agenda(L):
    L.add(Agendamento(conector=AG, horario="02:30", ativo=False, ordem=99))
    L.commit()
    r = pg.fonte(L, F, AGORA)
    assert r["status_texto"] == "Pausada" and r["motivo"] == "Agenda desligada"


# ---- PP2/3
def test_pp2_sem_total_sem_pct(L):
    ex(L, lidos=2500, lotes=3)
    cp(L, "ZZ_A", 2500)
    p = pg.fonte(L, F, AGORA)["progresso"]
    assert p["percentual"] is None and p["lotes_total"] is None and p["origem_total"] is None
    assert p["previsao_termino_brt"] is None and p["calculando"] is False


def test_pp2_total_parcial_sem_pct(L):
    ex(L, lidos=2500, lotes=3)
    cp(L, "ZZ_A", 2000, total=10000)
    cp(L, "ZZ_B", 500)  # uma tabela sem total
    p = pg.fonte(L, F, AGORA)["progresso"]
    print("PARCIAL", p["percentual"], p["origem_total"])
    assert p["percentual"] is None


def test_pp3_contagem(L, monkeypatch):
    cfg(monkeypatch, carga_lote=1000)
    ex(L, lidos=3000, lotes=3)
    cp(L, "ZZ_A", 3000, total=10000)
    p = pg.fonte(L, F, AGORA)["progresso"]
    assert (p["lote"], p["lotes_total"], p["percentual"]) == (3, 10, 30) and "contagem" in p["origem_total"]


def test_pp3_ultima_carga(L, monkeypatch):
    cfg(monkeypatch, carga_lote=1000)
    ex(L, status="sucesso", lidos=8000, lotes=8, finalizado_em=AGORA - timedelta(days=1))
    ex(L, lidos=2000, lotes=2)
    p = pg.fonte(L, F, AGORA)["progresso"]
    assert p["percentual"] == 25 and p["origem_total"] == "estimado pela última carga"


def test_pp3_educacao_nunca_ultima_carga(L, monkeypatch):
    fe = pg.Fonte("educacao", "E", (SIS,), (SIS,), None, None)
    ex(L, status="sucesso", lidos=8000, lotes=8)
    ex(L, lidos=2000, lotes=2)
    assert pg.fonte(L, fe, AGORA)["progresso"]["percentual"] is None


# ---- PP4
def test_pp4_zero(L):
    ex(L, lidos=10, lotes=1)
    cp(L, "ZZ_A", 10)
    cp(L, "ZZ_VAZIA", 0, conc=True)
    t = {x["tabela"]: x for x in pg.fonte(L, F, AGORA)["tabelas"]}
    assert t["ZZ_VAZIA"]["linhas_processadas"] == 0 and t["ZZ_VAZIA"]["estado"] == "concluida"


# ---- PP5
def test_pp5_calculando_e_eta(L, monkeypatch):
    cfg(monkeypatch, carga_lote=1000)
    e = ex(L, lidos=2000, lotes=2)
    cp(L, "ZZ_A", 2000, total=10000)
    p = pg.fonte(L, F, AGORA)["progresso"]
    assert p["calculando"] and p["previsao_termino_brt"] is None
    L.execute(text("update controle.execucoes set lotes=3, lidos=3000 where id=:i"), {"i": e.id})
    L.execute(text("update controle.checkpoints_carga set posicao=3000 where sistema='ZZTESTE'")) if False else None
    L.query(CheckpointCarga).filter_by(sistema=SIS).update({"posicao": 3000})
    L.commit()
    L.expire_all()
    p = pg.fonte(L, F, AGORA)["progresso"]
    # 30 min / 3 lotes = 10 min por lote; faltam 7 -> +70 min = 19:10 UTC = 16:10 BRT
    assert not p["calculando"] and p["previsao_termino_brt"] == "16:10", p


# ---- PP6
def test_pp6_limiar(L, monkeypatch):
    cfg(monkeypatch, ge_orfa_min=30)
    e = ex(L, atualizado_em=AGORA - timedelta(minutes=30))
    p = pg.fonte(L, F, AGORA)["progresso"]
    assert p["sem_sinal_min"] == 30 and not p["possivel_travada"]
    L.query(Execucao).filter_by(id=e.id).update({"atualizado_em": AGORA - timedelta(minutes=31)})
    L.commit()
    L.expire_all()
    r = pg.fonte(L, F, AGORA)
    assert r["progresso"]["possivel_travada"] and r["status"] == "rodando"
    cfg(monkeypatch, ge_orfa_min=45)
    assert not pg.fonte(L, F, AGORA)["progresso"]["possivel_travada"]


# ---- PP7 PII
@pytest.mark.parametrize("st", ["falha", "pausada", "cancelada", "interrompida", "parcial"])
def test_pp7_sem_pii(L, client, tokens, st):
    try:
        raise RuntimeError(PII)
    except RuntimeError as exc:
        msg = str(exc)
    ex(
        L,
        status=st,
        finalizado_em=AGORA,
        mensagem=msg,
        pausa_motivo=msg + " disco",
        erro_classe="conexao",
        erro_tabela="ZZ_A " + msg[:40],
    )
    r = pg.fonte(L, pg.Fonte("zzteste", "Z", (SIS,), (SIS,), None, None), AGORA)
    j = json.dumps(r, default=str)
    for frag in ("529.982", "Fulano", "senha", "ZZsegredo", "Traceback", "pyodbc"):
        assert frag not in j, (st, frag)
    assert r["motivo"] and r["motivo"] in pg.MOTIVOS.values()
    api = client.get("/api/fontes/progresso", headers=tokens["leitura"]).text
    for frag in ("529.982", "Fulano", "ZZsegredo", "Traceback"):
        assert frag not in api


# ---- PP8 perf
def test_pp8_perf_10k(client, tokens, L):
    L.execute(
        text("""insert into controle.execucoes (tipo, sistema, status, usuario, iniciado_em, finalizado_em, lidos, lotes)
      select 'carga_completa_conector', (array['ZZTESTE','ZZTESTE2','GESTAOESCOLAR','SIL','SAUDE'])[1+g%5],
             (array['sucesso','falha','cancelada'])[1+g%3], 'zzteste@aion.local', now()-g*interval '1 min', now()-g*interval '1 min', g, 1
      from generate_series(1,10000) g""")
    )
    L.commit()
    ex(L, lidos=500, lotes=1)
    cp(L, "ZZ_A", 500, total=1000)
    L.execute(text("analyze controle.execucoes"))
    L.commit()
    ts = []
    for _ in range(25):
        t0 = time.perf_counter()
        r = client.get("/api/fontes/progresso", headers=tokens["leitura"])
        ts.append((time.perf_counter() - t0) * 1000)
        assert r.status_code == 200
    ts.sort()
    print(
        f"PERF ms med={statistics.median(ts):.1f} p95={ts[int(0.95 * len(ts)) - 1]:.1f} max={ts[-1]:.1f} n_fontes={len(r.json()['fontes'])}"
    )
    L.execute(text("delete from controle.execucoes where usuario='zzteste@aion.local'"))
    L.commit()
    assert statistics.median(ts) < 200


# ---- PP10
def test_pp10_sei(L):
    r = [f for f in pg.painel(L, AGORA)["fontes"] if f["id"] == "sei"][0]
    print("SEI", r["status_texto"], r["falta"])
    assert r["status_texto"] == "Não liberada" and r["falta"]
