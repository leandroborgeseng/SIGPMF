# Roda depois de test_zzz_carga_completa (bronze SAUDE/CEMITERIO sintética já gravada). Só dados sintéticos.
"""CC7: etapas da carga completa, sinal de vida em conexão separada, carga interrompida (startup e thread morta),
retomada só de prata/ouro sem reler a origem e regressão do índice da prata (mesmo resultado do algoritmo linear)."""
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, text

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.sql_guard import garante_somente_leitura
from app.models.camadas import RegistroBronze, Vinculo
from app.models.controle import CheckpointCarga, Execucao, Log
from app.services import carga_completa as ccomp
from app.silver import processamento
from tests import sintetico_prata
from tests.test_zzz_carga_completa import _amostra, _ultima, origem  # noqa: F401 - fixture reaproveitada


def _bronze_assinatura(db, sistema):
    db.expire_all()
    n = db.scalar(select(func.count()).select_from(RegistroBronze).where(RegistroBronze.sistema_origem == sistema))
    h = db.scalar(text("select md5(string_agg(id::text || ':' || hash, ',' order by id)) from bronze.registros "
                       "where sistema_origem = :s"), {"s": sistema})
    return n, h


def _carga_ok(client, tokens, db, conector="saude"):
    _amostra(db, conector)
    assert client.post(f"/api/conectores/{conector}/completa", headers=tokens["admin"]).status_code == 202
    ex = _ultima(db, ccomp._sistema(conector))
    assert ex.status == "sucesso", ex.mensagem
    return ex


def _em_andamento(db, sistema, **kw):
    ex = Execucao(tipo=ccomp.TIPO, sistema=sistema, status="em_andamento", usuario="admin@aion.local", **kw)
    db.add(ex)
    db.commit()
    return ex


# ---------- CC7.1 / CC7.5 / API: etapas, contadores, atualizado_em e logs ----------
def test_cc7_api_etapas_e_logs_de_inicio_e_fim(client, tokens, origem, db):  # noqa: F811
    vistos = []
    original = ccomp.sinal_de_vida

    def espiao(ex_id, etapa, n, total):
        original(ex_id, etapa, n, total)
        outra = SessionLocal()  # outra conexão: enxerga o sinal enquanto a transação da prata ainda está aberta
        try:
            e = outra.get(Execucao, ex_id)
            vistos.append((e.etapa, e.processadas_etapa, e.total_etapa, e.atualizado_em is not None, e.status))
        finally:
            outra.close()
    from sqlalchemy import delete

    from app.models.controle import CamadasMarca
    db.execute(delete(CamadasMarca))  # Parte 2: sem marca d'água -> recomposição completa (prata e ouro com etapas)
    db.commit()
    pytest.MonkeyPatch.setattr(pytest.MonkeyPatch(), ccomp, "sinal_de_vida", espiao)
    try:
        ex = _carga_ok(client, tokens, db)
    finally:
        ccomp.sinal_de_vida = original
    prata = [v for v in vistos if v[0] == "prata"]
    assert prata and all(v[3] and v[4] == "em_andamento" for v in vistos)
    assert prata[-1][1] == prata[-1][2] > 0  # contador da prata chega ao total
    assert vistos[-1][0] == "ouro"  # etapa do ouro visível por outra conexão durante a transação
    s = client.get("/api/conectores/saude/completa", headers=tokens["admin"]).json()["execucao"]
    for k in ("etapa", "processadas_etapa", "total_etapa", "atualizado_em"):
        assert k in s
    assert s["etapa"] == "concluida" and s["etapa_nome"] == "Concluída" and s["atualizado_em"]
    assert s["status_texto"] == "Concluída · Carga completa" and s["sem_progresso"] is False
    msgs = [m for (m,) in db.execute(select(Log.mensagem).where(Log.execucao_id == ex.id))]
    for nome in ("Lendo da origem", "Tratando (prata)", "Unificando cidadãos (ouro)"):
        assert any(m.startswith(f"Etapa {nome}: início") for m in msgs), nome
        assert any(m.startswith(f"Etapa {nome}: fim em") for m in msgs), nome


def test_cc7_status_legivel_em_andamento(client, tokens, db):
    ex = _em_andamento(db, "CEMITERIO", etapa="leitura", processadas_etapa=4, total_etapa=10,
                       atualizado_em=datetime.now(UTC))
    trava = db.get_bind().connect()
    trava.execute(text("select pg_advisory_lock(:k)"), {"k": ccomp._chave_lock("CEMITERIO")})
    try:
        s = client.get("/api/conectores/cemiterio/completa", headers=tokens["admin"]).json()["execucao"]
        assert s["status_texto"] == "Em andamento · Carga completa" and s["etapa_nome"] == "Lendo da origem"
        assert (s["processadas_etapa"], s["total_etapa"]) == (4, 10) and s["alerta_min"] == 5
    finally:
        trava.execute(text("select pg_advisory_unlock(:k)"), {"k": ccomp._chave_lock("CEMITERIO")})
        trava.close()
        db.refresh(ex)
        ex.status = "cancelada"
        db.commit()


# ---------- CC7.3: sem progresso (env, padrão 5 min) ----------
def test_cc7_3_sem_progresso_por_env_e_thread_viva(client, tokens, db, monkeypatch):
    ex = _em_andamento(db, "CEMITERIO", etapa="prata", atualizado_em=datetime.now(UTC) - timedelta(minutes=10))
    trava = db.get_bind().connect()  # processo vivo segura a trava
    trava.execute(text("select pg_advisory_lock(:k)"), {"k": ccomp._chave_lock("CEMITERIO")})
    try:
        s = client.get("/api/conectores/cemiterio/completa", headers=tokens["admin"]).json()["execucao"]
        assert s["status"] == "em_andamento" and s["sem_progresso"] is True
        monkeypatch.setenv("SIGPMF_CARGA_ALERTA_MIN", "30")
        get_settings.cache_clear()
        s = client.get("/api/conectores/cemiterio/completa", headers=tokens["admin"]).json()["execucao"]
        assert s["sem_progresso"] is False and s["alerta_min"] == 30
    finally:
        get_settings.cache_clear()
        trava.execute(text("select pg_advisory_unlock(:k)"), {"k": ccomp._chave_lock("CEMITERIO")})
        trava.close()
    monkeypatch.delenv("SIGPMF_CARGA_ALERTA_MIN")
    get_settings.cache_clear()
    # trava livre + sem progresso = thread morta: vira "Carga interrompida"
    s = client.get("/api/conectores/cemiterio/completa", headers=tokens["admin"]).json()["execucao"]
    assert s["status"] == "interrompida" and s["status_texto"] == "Carga interrompida" and s["sem_progresso"] is False
    db.expire_all()
    assert db.get(Execucao, ex.id).status == "interrompida"


# ---------- TL: startup marca em_andamento sem processo vivo como interrompida ----------
def test_startup_em_andamento_sem_processo_vivo_vira_interrompida(client, tokens, db):
    from app.main import interromper_cargas_orfas
    orfa = _em_andamento(db, "SAUDE", etapa="prata", atualizado_em=datetime.now(UTC))
    viva = _em_andamento(db, "CEMITERIO", etapa="leitura", atualizado_em=datetime.now(UTC))
    trava = db.get_bind().connect()  # Cemitério tem processo vivo (trava segura): não mexe
    trava.execute(text("select pg_advisory_lock(:k)"), {"k": ccomp._chave_lock("CEMITERIO")})
    try:
        assert interromper_cargas_orfas() == 1
        db.expire_all()
        assert db.get(Execucao, orfa.id).status == "interrompida"
        assert db.get(Execucao, viva.id).status == "em_andamento"
        s = client.get("/api/conectores/saude/completa", headers=tokens["admin"]).json()["execucao"]
        assert s["status"] == "interrompida" and s["status_texto"] == "Carga interrompida"
        assert "Carga interrompida" in s["mensagem"]
        assert db.scalar(select(func.count()).select_from(Log).where(Log.execucao_id == orfa.id, Log.nivel == "WARN")) == 1
    finally:
        trava.execute(text("select pg_advisory_unlock(:k)"), {"k": ccomp._chave_lock("CEMITERIO")})
        trava.close()
        v = db.get(Execucao, viva.id)
        v.status = "cancelada"
        db.commit()


def test_startup_lifespan_chama_interromper(monkeypatch):
    import app.main as m
    chamado = []
    monkeypatch.setattr(m, "interromper_cargas_orfas", lambda: chamado.append(1) or 0)
    from fastapi.testclient import TestClient
    with TestClient(m.create_app()):
        pass
    assert chamado == [1]


# ---------- TL: retomar prata/ouro não relê a origem e não altera a bronze ----------
def test_retomar_camadas_sem_reler_origem_e_bronze_inalterada(client, tokens, origem, db, monkeypatch):  # noqa: F811
    ex = _carga_ok(client, tokens, db)
    assert client.post("/api/conectores/saude/completa/retomar-camadas",
                       headers=tokens["admin"]).status_code == 409  # não interrompida: recusa
    ex = db.get(Execucao, ex.id)
    ex.status = "interrompida"  # simula a thread morta na prata (bronze toda gravada)
    db.commit()
    db.execute(text("delete from silver.vinculos where sistema = 'SAUDE'"))  # prata parcial/antiga
    # Parte 2 (PI8): sem marca d'água (caiu antes da 1ª marca), a retomada é recomposição; com marca, segue a fila
    db.execute(text("delete from controle.camadas_marca"))
    db.commit()
    s = client.get("/api/conectores/saude/completa", headers=tokens["admin"]).json()
    assert s["execucao"]["pode_retomar_camadas"] is True
    antes = _bronze_assinatura(db, "SAUDE")
    cps_antes = sorted((c.tabela, c.posicao, c.concluida, repr(c.ultima_chave)) for c in db.scalars(select(CheckpointCarga)))
    o = origem["SAUDE"]
    o.sqls.clear()

    def proibido(*a, **k):
        raise AssertionError("conector não pode ser chamado na retomada de prata/ouro")
    monkeypatch.setattr(ccomp, "leitor_padrao", proibido)
    monkeypatch.setattr(type(o), "abrir", proibido)
    r = client.post("/api/conectores/saude/completa/retomar-camadas", headers=tokens["admin"])
    assert r.status_code == 202, r.text
    novo = _ultima(db, "SAUDE")
    assert novo.id != ex.id and novo.status == "sucesso" and novo.etapa == "concluida", novo.mensagem
    assert o.sqls == [] and novo.lidos == 0  # nada lido da origem
    assert _bronze_assinatura(db, "SAUDE") == antes  # contagem e hash da bronze iguais
    db.expire_all()
    assert sorted((c.tabela, c.posicao, c.concluida, repr(c.ultima_chave))
                  for c in db.scalars(select(CheckpointCarga))) == cps_antes
    assert db.scalar(select(func.count()).select_from(Vinculo).where(Vinculo.sistema == "SAUDE")) > 0  # prata refeita
    assert any("sem reler a origem" in m for (m,) in db.execute(select(Log.mensagem).where(Log.execucao_id == novo.id)))


# ---------- Regressão: índice da prata == algoritmo linear antigo ----------
def _compara(db):
    processamento.reconstruir(db, indexado=False)
    antigo = sintetico_prata.snapshot(db)
    processamento.reconstruir(db, indexado=True)
    novo = sintetico_prata.snapshot(db)
    return antigo, novo


def test_regressao_indice_igual_ao_linear_no_fixture(db):
    try:
        antigo, novo = _compara(db)  # bronze do fixture saude+cemiterio (óbitos, CNS, terceiros, e-SUS)
        assert antigo["vinculos"] and antigo == novo
    finally:
        db.rollback()


def test_regressao_indice_igual_ao_linear_sintetico_com_colisoes(db):
    try:
        sintetico_prata.gera(db, 5000)  # CC8.1: ~5 mil sem CPF
        antigo, novo = _compara(db)
        assert any(p[3] == "revisao" for p in antigo["pendentes"])  # há colisões de verdade
        assert any(v[4] == "novo_sem_cpf" and 0 < v[5] < 0.85 for v in antigo["vinculos"])  # melhor parcial gravado
        assert antigo == novo
    finally:
        db.rollback()


# ---------- CC8.4 / CC8.5: retomada idempotente; queda no meio devolve prata/ouro ao estado anterior ----------
def _camadas_snapshot(db):
    db.expire_all()
    s = sintetico_prata.snapshot(db)
    s["gold"] = db.scalar(text("select md5(coalesce(string_agg(cidadao_id || campo || valor, ',' order by cidadao_id, campo, "
                               "valor), '')) from gold.cidadao_dados"))
    return s


def test_cc8_4_retomar_duas_vezes_mesmo_resultado(client, tokens, origem, db):  # noqa: F811
    ex = _carga_ok(client, tokens, db)
    resultados = []
    for _ in range(2):
        e = _ultima(db, "SAUDE")
        e.status = "interrompida"
        db.commit()
        assert client.post("/api/conectores/saude/completa/retomar-camadas", headers=tokens["admin"]).status_code == 202
        assert _ultima(db, "SAUDE").status == "sucesso"
        resultados.append((_camadas_snapshot(db), _bronze_assinatura(db, "SAUDE")))
    assert resultados[0] == resultados[1] and ex.id < _ultima(db, "SAUDE").id


def test_cc8_5_queda_no_ouro_bronze_intacta_e_prata_ouro_voltam(client, tokens, origem, db, monkeypatch):  # noqa: F811
    _carga_ok(client, tokens, db)
    e = _ultima(db, "SAUDE")
    e.status = "interrompida"
    db.execute(text("delete from controle.camadas_marca"))  # Parte 2: força o caminho da recomposição (ouro cai)
    db.commit()
    antes, bronze = _camadas_snapshot(db), _bronze_assinatura(db, "SAUDE")
    from app.gold import visao

    def cai(_db):
        raise MemoryError("queda sintética no ouro")
    monkeypatch.setattr(visao, "reconstruir", cai)
    assert client.post("/api/conectores/saude/completa/retomar-camadas", headers=tokens["admin"]).status_code == 202
    ex = _ultima(db, "SAUDE")
    assert ex.status == "falha"
    assert _camadas_snapshot(db) == antes and _bronze_assinatura(db, "SAUDE") == bronze


# ---------- CC9: quarentena de chave nula ----------
@pytest.fixture
def limite_50(monkeypatch):
    monkeypatch.setenv("SIGPMF_CARGA_NULOS_LIMITE_PCT", "50")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def exige_banco_de_teste(nome: str | None) -> None:
    """Trava: só apaga dados se o banco conectado for de teste (nome contém '_test'). Nunca em produção/HEF."""
    if not nome or "_test" not in nome.lower():
        raise RuntimeError(f"Recusado: _isola_bronze só roda em banco de teste (nome com '_test'); banco={nome!r}")


def _isola_bronze(db, sistema):
    """Banco de TESTE: zera a prata/ouro (derivadas, reconstruídas pela carga), a bronze e os checkpoints do sistema."""
    exige_banco_de_teste(db.execute(text("select current_database()")).scalar())
    for t in ("silver.enderecos", "silver.vinculos", "silver.identificadores", "silver.correlacoes_pendentes",
              "silver.inconsistencias", "gold.cidadao_dados", "gold.cidadao_sistemas"):
        db.execute(text(f"delete from {t}"))
    db.execute(text("delete from bronze.registros where sistema_origem = :s"), {"s": sistema})
    db.execute(text("delete from controle.checkpoints_carga where sistema = :s"), {"s": sistema})
    db.commit()


def test_cc9_chave_nula_vai_para_quarentena_e_reconcilia_com_pendencias(client, tokens, origem, db, limite_50):  # noqa: F811
    from app.connectors.sqlserver.politicas import politica
    _isola_bronze(db, "CEMITERIO")  # só a bronze desta carga conta (sem sobras de outros módulos)
    _amostra(db, "cemiterio")
    o = origem["CEMITERIO"]
    base = o.mock.ler("CEMITERIO", "Obitos", None, politica("CEMITERIO").colunas_select("Obitos"))
    nome_sint = "PESSOA SINTETICA QUARENTENA"
    o.extras["Obitos"] = [{**base[0], "SEPULTURA": "S-NULA", "GAVETA": None, "NOME": nome_sint}]
    try:
        for rodada in range(2):  # 2ª rodada: a linha em quarentena não duplica
            o.sqls.clear()
            o.nulos_lidos = 0
            assert client.post("/api/conectores/cemiterio/completa", headers=tokens["admin"]).status_code == 202
            ex = _ultima(db, "CEMITERIO")
            assert ex.status == "sucesso", ex.mensagem
            assert "1 linha(s) em quarentena (chave nula)" in ex.mensagem
            assert o.nulos_lidos == 1  # SELECT separado só na tabela com nulos
            nulas = [x for x, _ in o.sqls if "TOP (" in x and "IS NULL" in x and "IS NOT NULL" not in x]
            assert len(nulas) == 1 and nulas[0].startswith("SELECT TOP (1) ") and garante_somente_leitura(nulas[0])
            paginas = [x for x, _ in o.sqls if "TOP (" in x and x not in nulas]
            assert paginas and all("IS NOT NULL" in x for x in paginas)  # lotes só com chave completa
            q = list(db.scalars(select(RegistroBronze).where(RegistroBronze.sistema_origem == "CEMITERIO",
                                                               RegistroBronze.quarentena.is_not(None))))
            assert len(q) == 1 and q[0].tabela == "Obitos" and q[0].quarentena == "chave_nula"
            assert q[0].quarentena_campos == ["GAVETA"] and nome_sint not in q[0].id_origem
            # prata/ouro ignoram a linha em quarentena
            assert db.scalar(select(func.count()).select_from(Vinculo).where(Vinculo.bronze_id == q[0].id)) == 0
            s = client.get("/api/conectores/cemiterio/completa", headers=tokens["admin"]).json()
            assert s["quarentena"] == 1
            for t, r in s["reconciliacao"].items():  # igualdade exata: bronze_ok + quarentena == COUNT(*) da origem
                assert r["bronze"] + r["quarentena"] == r["origem"] == len(o.linhas(t)), (rodada, t, r)
                assert r["quarentena"] == (1 if t == "Obitos" else 0) and r["ok"] is True
                assert r["status"] == ("ok_com_pendencias" if t == "Obitos" else "ok")
            assert "reconciliacao=ok com pendências" in ex.mensagem
            # keyset: nenhuma linha de chave completa perdida nem repetida (todas as chaves completas na bronze)
            ord_o = ccomp.ordem("CEMITERIO", "Obitos")
            from app.bronze.ingestao import _id
            from app.ingestion.mapeamentos import MAPEAMENTOS
            from app.services.diagnostico import linha_json
            cols = politica("CEMITERIO").colunas_select("Obitos")
            completas = {_id(MAPEAMENTOS[("CEMITERIO", "Obitos")]["chave"], linha_json(x, cols)) for x in o.linhas("Obitos")
                         if all(x.get(c) is not None for c in ord_o)}
            ids = set(db.scalars(select(RegistroBronze.id_origem).where(
                RegistroBronze.sistema_origem == "CEMITERIO", RegistroBronze.tabela == "Obitos",
                RegistroBronze.quarentena.is_(None))))
            assert len(completas) == len(o.linhas("Obitos")) - 1 and completas <= ids
        # detalhe só admin, sem valores da linha
        assert client.get("/api/conectores/cemiterio/completa/quarentena", headers=tokens["operador"]).status_code == 403
        r = client.get("/api/conectores/cemiterio/completa/quarentena", headers=tokens["admin"])
        assert r.status_code == 200
        linhas = r.json()["linhas"]
        assert [(x["tabela"], x["motivo"], x["campos_nulos"]) for x in linhas] == [("Obitos", "Chave nula na origem", ["GAVETA"])]
        corpo = r.text
        assert nome_sint not in corpo and "S-NULA" not in corpo and "cpf" not in corpo.lower()
    finally:
        o.extras.clear()


def test_cc9_duplicatas_identicas_de_chave_nula_nao_colapsam(client, tokens, origem, db, limite_50):  # noqa: F811
    from app.connectors.sqlserver.politicas import politica
    _isola_bronze(db, "CEMITERIO")
    _amostra(db, "cemiterio")
    o = origem["CEMITERIO"]
    base = o.mock.ler("CEMITERIO", "Obitos", None, politica("CEMITERIO").colunas_select("Obitos"))
    nula = {**base[0], "SEPULTURA": "S-NULA", "GAVETA": None, "NOME": "PESSOA SINTETICA DUPLICADA"}
    o.extras["Obitos"] = [dict(nula), dict(nula)]  # duas linhas idênticas com chave nula
    try:
        for _ in range(2):  # reexecução idempotente: continua 2, sem duplicar
            assert client.post("/api/conectores/cemiterio/completa", headers=tokens["admin"]).status_code == 202
            assert _ultima(db, "CEMITERIO").status == "sucesso"
            q = list(db.scalars(select(RegistroBronze.id_origem).where(
                RegistroBronze.sistema_origem == "CEMITERIO", RegistroBronze.quarentena.is_not(None))))
            assert len(q) == len(set(q)) == 2 and all(x.endswith((":1", ":2")) for x in q)
            r = client.get("/api/conectores/cemiterio/completa", headers=tokens["admin"]).json()["reconciliacao"]["Obitos"]
            assert r["quarentena"] == 2 and r["bronze"] + r["quarentena"] == r["origem"] == len(o.linhas("Obitos"))
            assert r["ok"] is True and r["status"] == "ok_com_pendencias"
    finally:
        o.extras.clear()


def test_cc9_3_acima_do_limite_para_com_a_mensagem_atual(client, tokens, origem, db, monkeypatch):  # noqa: F811
    from app.connectors.sqlserver.politicas import politica
    monkeypatch.setenv("SIGPMF_CARGA_NULOS_LIMITE_PCT", "1")
    monkeypatch.setenv("SIGPMF_CARGA_NULOS_LIMITE_MIN", "0")  # sem mínimo absoluto: 1 nula em poucas linhas para
    get_settings.cache_clear()
    _amostra(db, "cemiterio")
    o = origem["CEMITERIO"]
    base = o.mock.ler("CEMITERIO", "Obitos", None, politica("CEMITERIO").colunas_select("Obitos"))
    o.extras["Obitos"] = [{**base[0], "SEPULTURA": "S-NULA", "GAVETA": None}]  # 1 em poucas linhas: > 1%
    try:
        o.sqls.clear()
        assert client.post("/api/conectores/cemiterio/completa", headers=tokens["admin"]).status_code == 202
        ex = _ultima(db, "CEMITERIO")
        assert ex.status == "falha" and "a carga completa para" in ex.mensagem and "limite de 1%" in ex.mensagem
        assert not [x for x, _ in o.sqls if "TOP (" in x]
    finally:
        o.extras.clear()
        get_settings.cache_clear()


def test_cc9_reconciliacao_isolada_bronze_mais_quarentena_igual_origem(db):
    sis = "TESTE_QUARENTENA"
    ex = Execucao(tipo=ccomp.TIPO, sistema=sis, status="sucesso", usuario="t")
    db.add(ex)
    db.flush()
    db.add_all([CheckpointCarga(sistema=sis, tabela=t, posicao=4, total_origem=4, concluida=True, execucao_id=ex.id)
                for t in ("com_pendencia", "faltando")])
    for t, n, q in (("com_pendencia", 3, 1), ("faltando", 2, 1)):
        for i in range(n):
            db.add(RegistroBronze(sistema_origem=sis, tabela=t, id_origem=str(i), dados={"i": i}, hash=f"h{i}", execucao_id=ex.id))
        for i in range(q):
            db.add(RegistroBronze(sistema_origem=sis, tabela=t, id_origem=f"chave_nula:q{i}", dados={}, hash=f"q{i}",
                                  execucao_id=ex.id, quarentena="chave_nula", quarentena_campos=["k"]))
    db.commit()
    r = ccomp.reconciliacao(db, sis)
    assert r["com_pendencia"] == {"origem": 4, "bronze": 3, "quarentena": 1, "ocultas": 0, "ok": True, "status": "ok_com_pendencias"}
    assert r["faltando"] == {"origem": 4, "bronze": 2, "quarentena": 1, "ocultas": 0, "ok": False, "status": "divergente"}


def test_cc9_limite_proporcional():
    v = {"total": 1000, "nulos": 10, "distintas": 990}
    assert ccomp.problema_chave("T", ("a",), v) is None  # 1,0%: no limite, quarentena
    msg = ccomp.problema_chave("T", ("a",), {**v, "nulos": 11, "distintas": 989})
    assert "a carga completa para" in msg and "1.10%" in msg and "limite de 1%" in msg and "mínimo de 10" in msg
    assert "não é única" in ccomp.problema_chave("T", ("a",), {**v, "distintas": 989})


def test_cc9_minimo_absoluto_tabela_pequena_nao_bloqueia():
    # Leilao sintética: 50 linhas, 1 nula = 2% (> 1%), mas 1 <= mínimo 10 -> quarentena
    assert ccomp.problema_chave("Leilao", ("a",), {"total": 50, "nulos": 1, "distintas": 49}) is None
    assert ccomp.problema_chave("Leilao", ("a",), {"total": 50, "nulos": 10, "distintas": 40}) is None
    # acima dos dois limites (> 1% e > 10): ainda bloqueia
    msg = ccomp.problema_chave("Leilao", ("a",), {"total": 50, "nulos": 11, "distintas": 39})
    assert msg and "22.00%" in msg and "limite de 1%" in msg and "mínimo de 10" in msg
    # > 10 nulos mas <= 1%: quarentena
    assert ccomp.problema_chave("T", ("a",), {"total": 5000, "nulos": 40, "distintas": 4960}) is None


def test_cc9_minimo_absoluto_por_env(monkeypatch):
    monkeypatch.setenv("SIGPMF_CARGA_NULOS_LIMITE_MIN", "0")
    get_settings.cache_clear()
    try:
        msg = ccomp.problema_chave("Leilao", ("a",), {"total": 50, "nulos": 1, "distintas": 49})
        assert msg and "2.00%" in msg and "mínimo de 0" in msg
        monkeypatch.setenv("SIGPMF_CARGA_NULOS_LIMITE_MIN", "25")
        get_settings.cache_clear()
        assert ccomp.problema_chave("Leilao", ("a",), {"total": 50, "nulos": 20, "distintas": 30}) is None
        assert ccomp.problema_chave("Leilao", ("a",), {"total": 50, "nulos": 26, "distintas": 24})
    finally:
        get_settings.cache_clear()


def test_cc9_leilao_50_linhas_1_nula_termina_ok_com_pendencias(client, tokens, origem, db, monkeypatch):  # noqa: F811
    """Regressão do caso real (sintético): Leilao pequena com 1 chave nula não para a carga com o padrão 1% + mín. 10."""
    from app.connectors.sqlserver.politicas import politica
    monkeypatch.delenv("SIGPMF_CARGA_NULOS_LIMITE_PCT", raising=False)
    monkeypatch.delenv("SIGPMF_CARGA_NULOS_LIMITE_MIN", raising=False)
    get_settings.cache_clear()
    _isola_bronze(db, "CEMITERIO")
    _amostra(db, "cemiterio")
    o = origem["CEMITERIO"]
    base = o.mock.ler("CEMITERIO", "Leilao", None, politica("CEMITERIO").colunas_select("Leilao"))
    falta = 50 - len(base)
    extras = [{**base[0], "Quadra": "QZ", "Sepultura": f"ZZ{i:03d}", "Nome": f"ZZTESTE LEILAO {i:03d}",
               "CPF": None} for i in range(falta - 1)]
    extras.append({**base[0], "Quadra": "QZ", "Sepultura": "ZZNULA", "DataVenda": None,
                   "Nome": "ZZTESTE LEILAO NULA", "CPF": None})
    o.extras["Leilao"] = extras
    try:
        assert len(o.linhas("Leilao")) == 50
        assert client.post("/api/conectores/cemiterio/completa", headers=tokens["admin"]).status_code == 202
        ex = _ultima(db, "CEMITERIO")
        assert ex.status == "sucesso", ex.mensagem
        assert "1 linha(s) em quarentena (chave nula)" in ex.mensagem
        s = client.get("/api/conectores/cemiterio/completa", headers=tokens["admin"]).json()
        assert s["quarentena"] == 1
        r = s["reconciliacao"]["Leilao"]
        assert r["bronze"] + r["quarentena"] == r["origem"] == 50
        assert r["quarentena"] == 1 and r["ok"] is True and r["status"] == "ok_com_pendencias"
        q = list(db.scalars(select(RegistroBronze).where(RegistroBronze.sistema_origem == "CEMITERIO",
                                                           RegistroBronze.quarentena.is_not(None))))
        assert len(q) == 1 and q[0].tabela == "Leilao" and q[0].quarentena_campos == ["DataVenda"]
        assert "ZZTESTE" not in q[0].id_origem
    finally:
        o.extras.clear()
        get_settings.cache_clear()


@pytest.mark.parametrize("nome", ["sigpmf", "sigpmf_prod", "hef", "postgres", "", None])
def test_isola_bronze_recusa_banco_que_nao_e_de_teste(nome):
    with pytest.raises(RuntimeError, match="banco de teste"):
        exige_banco_de_teste(nome)


@pytest.mark.parametrize("nome", ["sigpmf_test", "sigpmf_mun_ress_teste", "SIGPMF_TEST_CI"])
def test_isola_bronze_aceita_banco_de_teste(nome):
    exige_banco_de_teste(nome)


def test_isola_bronze_confere_o_banco_conectado(db, monkeypatch):
    class Falso:
        def execute(self, *a, **k):
            return type("R", (), {"scalar": lambda self: "sigpmf"})()
    with pytest.raises(RuntimeError):
        _isola_bronze(Falso(), "CEMITERIO")


def test_cc9_tabela_toda_nula_bloqueia_mesmo_abaixo_do_minimo():
    # ZZTESTE: 5 linhas, todas com chave nula (5 <= mínimo 10), ainda assim a carga para
    msg = ccomp.problema_chave("ZZTESTE_Tab", ("a",), {"total": 5, "nulos": 5, "distintas": 0})
    assert msg and "todas as 5 linha(s)" in msg and "100%" in msg and "a carga completa para" in msg
    assert ccomp.problema_chave("ZZTESTE_Tab", ("a",), {"total": 5, "nulos": 4, "distintas": 1}) is None
    assert ccomp.problema_chave("ZZTESTE_Tab", ("a",), {"total": 0, "nulos": 0, "distintas": 0}) is None
