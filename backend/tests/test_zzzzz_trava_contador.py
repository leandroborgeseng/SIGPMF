"""CC11 (contador da prata) e CC12 (trava órfã). Só dados sintéticos ZZTESTE, banco *_test."""
import zlib
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, text

from app.core.db import SessionLocal
from app.models.camadas import RegistroBronze, Vinculo
from app.models.controle import Auditoria, Execucao
from app.services import carga_completa as ccomp
from app.services import trava as tv
from app.silver import processamento
from tests.test_zzz_carga_completa import _amostra, _ultima, origem  # noqa: F401 - fixture reaproveitada

USR = "zzteste-trava@aion.local"


def _banco_teste(db):
    nome = db.execute(text("select current_database()")).scalar()
    assert nome and "_test" in nome.lower(), nome


def _fecha(db):
    for e in db.scalars(select(Execucao).where(Execucao.usuario == USR, Execucao.status == "em_andamento")):
        e.status = "falha"  # encerra as execuções sintéticas (sem apagar nada)
    db.commit()


@pytest.fixture
def lock_de_outro():
    """Outra sessão segura a trava do SAUDE (= processo vivo)."""
    outra = SessionLocal()
    k = zlib.crc32(b"amostra:SAUDE")
    assert outra.scalar(text("select pg_try_advisory_lock(:k)"), {"k": k})
    yield
    outra.execute(text("select pg_advisory_unlock(:k)"), {"k": k})
    outra.close()


def _exec(db, tipo="amostra_conector", minutos=0, sistema="SAUDE"):
    quando = datetime.now(UTC) - timedelta(minutes=minutos)
    ex = Execucao(tipo=tipo, sistema=sistema, status="em_andamento", usuario=USR, iniciado_em=quando, atualizado_em=quando)
    db.add(ex)
    db.commit()
    return ex


# ---------- CC11: contador da prata ----------
def test_cc11_contador_conta_cada_registro_uma_vez_e_nunca_passa_do_total(db):
    _banco_teste(db)
    ex = Execucao(tipo="zzteste_contador", sistema="SAUDE", status="sucesso", usuario=USR)
    db.add(ex)
    db.flush()
    sp = db.begin_nested()
    n = 6000  # sem CPF/CNS: antes contavam 2x (etapa 1 e etapa 3) -> "10.000 de 6.000"
    db.add_all([RegistroBronze(sistema_origem="SAUDE", tabela="pacientes", id_origem=f"ZZTESTE{i}",
                               dados={"Matricula": f"ZZTESTE{i}", "Nome": f"ZZTESTE PESSOA {i}", "Cpf": None},
                               hash=f"zz{i}", execucao_id=ex.id) for i in range(n)])
    db.flush()
    vistos = []
    r = processamento.reconstruir(db, progresso=lambda a, t: vistos.append((a, t)))
    total = vistos[-1][1]
    assert total >= n and all(a <= t == total for a, t in vistos), vistos
    assert vistos[-1] == (total, total) and r["tratados"] == r["total_bronze"] == total
    linhas = db.scalar(select(func.count()).select_from(Vinculo))
    chaves = db.scalar(select(func.count(func.distinct(func.concat(Vinculo.sistema, "|", Vinculo.tabela, "|",
                                                                   Vinculo.chave_origem)))))
    assert linhas == chaves  # sem duplicação real
    sp.rollback()
    db.rollback()


def test_cc11_confere_prata_recusa_duplicata():
    class Falso:
        def __init__(self, valores):
            self.v = iter(valores)

        def scalar(self, *_a, **_k):
            return next(self.v)
    ccomp.confere_prata(Falso([10, 10, 3, 3]))
    with pytest.raises(ccomp.PrataInconsistente, match="vínculos: 12 linhas para 10 chaves distintas"):
        ccomp.confere_prata(Falso([12, 10, 3, 3]))


def test_cc11_prata_inconsistente_ouro_nao_roda_e_mensagem_clara(client, tokens, origem, db, monkeypatch):  # noqa: F811
    from app.gold import visao
    _amostra(db, "saude")
    chamou = []
    monkeypatch.setattr(visao, "reconstruir", lambda _db: chamou.append(1) or {})

    def inconsistente(_db):
        raise ccomp.PrataInconsistente("prata não bate com as chaves distintas (vínculos: 12 linhas para 10 chaves "
                                       "distintas); o ouro não foi gerado")
    monkeypatch.setattr(ccomp, "confere_prata", inconsistente)
    assert client.post("/api/conectores/saude/completa", headers=tokens["admin"]).status_code == 202
    ex = _ultima(db, "SAUDE")
    assert ex.status == "falha" and "o ouro não foi gerado" in ex.mensagem and "12 linhas para 10" in ex.mensagem
    assert not chamou


def test_cc11_situacao_traz_registros_gerados_na_prata(client, tokens, origem, db):  # noqa: F811
    _amostra(db, "saude")
    s = client.get("/api/conectores/saude/completa", headers=tokens["admin"]).json()
    assert isinstance(s["camadas"]["gerados_prata"], int) and s["camadas"]["gerados_prata"] >= s["camadas"]["prata"]


# ---------- CC12: trava órfã ----------
def test_cc12_amostra_orfa_vira_interrompida_auditada_e_bronze_mantida(client, tokens, origem, db):  # noqa: F811
    _amostra(db, "saude")
    bronze = db.scalar(select(func.count()).select_from(RegistroBronze).where(RegistroBronze.sistema_origem == "SAUDE"))
    velha = _exec(db, minutos=30)  # sobrou de um reinício: ninguém segura a trava
    try:
        _amostra(db, "saude")  # a nova inicia
        db.expire_all()
        v = db.get(Execucao, velha.id)
        assert v.status == "interrompida" and v.finalizado_em is not None
        a = db.scalar(select(Auditoria).where(Auditoria.acao == "execucao_interrompida", Auditoria.alvo == f"execucao:{v.id}"))
        assert a is not None and a.detalhe["automatico"] is True and a.detalhe["tipo"] == "amostra_conector"
        assert db.scalar(select(func.count()).select_from(RegistroBronze).where(
            RegistroBronze.sistema_origem == "SAUDE")) >= bronze  # bronze não apagada
    finally:
        _fecha(db)


def test_cc12_completa_inicia_com_amostra_orfa(client, tokens, origem, db):  # noqa: F811
    _amostra(db, "saude")
    velha = _exec(db, minutos=1)
    try:
        assert client.post("/api/conectores/saude/completa", headers=tokens["admin"]).status_code == 202
        db.expire_all()
        assert db.get(Execucao, velha.id).status == "interrompida"
        assert _ultima(db, "SAUDE").status == "sucesso"
    finally:
        _fecha(db)


def test_cc12_viva_e_recente_409_diz_qual_e_desde_quando(client, tokens, db, lock_de_outro):
    ex = _exec(db, tipo=ccomp.TIPO, minutos=1)
    try:
        r = client.post("/api/conectores/saude/amostra", headers=tokens["admin"])
        assert r.status_code == 409
        d = r.json()["detail"]
        assert "carga completa de Saúde em andamento desde" in d and "última atividade" in d
        db.expire_all()
        assert db.get(Execucao, ex.id).status == "em_andamento"
        b = client.get("/api/conectores/saude/bloqueio", headers=tokens["admin"]).json()["bloqueio"]
        assert b["tipo"] == "completa" and b["viva"] is True and b["parada"] is False and b["pode_interromper"] is False
        assert b["iniciado_em"] and b["atualizado_em"] and b["execucao_id"] == ex.id
        r = client.post(f"/api/conectores/saude/execucoes/{ex.id}/interromper", headers=tokens["admin"],
                        json={"motivo": "ZZTESTE parada"})
        assert r.status_code == 409  # com atividade recente: não interrompe
    finally:
        _fecha(db)


def test_cc12_trava_ocupada_nunca_interrompe_sozinha_mesmo_parada(client, tokens, db, lock_de_outro):
    """TL: com a trava ocupada (processo vivo), nem sem sinal de vida a execução é interrompida automaticamente."""
    ex = _exec(db, minutos=30)  # SIGPMF_CARGA_ALERTA_MIN padrão 5
    try:
        r = client.post("/api/conectores/saude/amostra", headers=tokens["admin"])
        d = r.json()["detail"]
        assert r.status_code == 409 and "amostra de Saúde em andamento desde" in d and "um admin pode interrompê-la" in d
        db.expire_all()
        v = db.get(Execucao, ex.id)
        assert v.status == "em_andamento" and not v.cancelar
        assert db.scalar(select(Auditoria).where(Auditoria.acao == "execucao_interrompida",
                                                 Auditoria.alvo == f"execucao:{ex.id}")) is None
    finally:
        _fecha(db)


def test_cc12_interromper_so_admin_com_motivo_auditado(client, tokens, db, lock_de_outro):
    ex = _exec(db, minutos=30)
    try:
        b = client.get("/api/conectores/saude/bloqueio", headers=tokens["operador"]).json()["bloqueio"]
        assert b["parada"] is True and b["pode_interromper"] is False  # operador não interrompe
        assert client.get("/api/conectores/saude/bloqueio", headers=tokens["admin"]).json()["bloqueio"]["pode_interromper"]
        url = f"/api/conectores/saude/execucoes/{ex.id}/interromper"
        assert client.post(url, headers=tokens["operador"], json={"motivo": "ZZTESTE parada"}).status_code == 403
        assert client.post(url, headers=tokens["admin"], json={"motivo": "x"}).status_code == 422
        r = client.post(url, headers=tokens["admin"], json={"motivo": "ZZTESTE parada sem atividade"})
        assert r.status_code == 200 and r.json()["status"] == "interrompida"
        a = db.scalar(select(Auditoria).where(Auditoria.acao == "execucao_interrompida", Auditoria.alvo == f"execucao:{ex.id}"))
        assert a.usuario == "admin@aion.local" and a.detalhe["motivo"] == "ZZTESTE parada sem atividade"
        assert a.detalhe["automatico"] is False
        assert client.post(url, headers=tokens["admin"], json={"motivo": "ZZTESTE de novo"}).status_code == 409
        assert client.get("/api/conectores/saude/bloqueio", headers=tokens["admin"]).json()["bloqueio"] is None
    finally:
        _fecha(db)


def test_cc12_startup_interrompe_amostra_orfa(db):
    ex = _exec(db, minutos=2)
    try:
        assert ccomp.interromper_orfas(db) >= 1
        db.expire_all()
        assert db.get(Execucao, ex.id).status == "interrompida"
    finally:
        _fecha(db)


def test_cc12_bases_traz_bloqueio_para_a_tela(client, tokens, db, lock_de_outro):
    _exec(db, minutos=1)
    try:
        bases = client.get("/api/conectores", headers=tokens["admin"]).json()
        bases = bases if isinstance(bases, list) else bases.get("bases", [])
        saude = [b for b in bases if b.get("conector_id") == "saude"]
        assert saude and saude[0]["bloqueio"]["tipo"] == "amostra" and saude[0]["bloqueio"]["viva"] is True
        assert tv.estado(db, "SAUDE", "Saúde")["mensagem"].startswith("Já existe uma amostra de Saúde")
    finally:
        _fecha(db)


# ---------- Completa órfã: 'pausada' (retomada do checkpoint não depende do status, mas a tela mostra "Retomar carga") ----------
def test_cc12_completa_orfa_vira_pausada_auditada(client, tokens, origem, db):  # noqa: F811
    _amostra(db, "saude")
    velha = _exec(db, tipo=ccomp.TIPO, minutos=30)
    try:
        assert client.post("/api/conectores/saude/amostra", headers=tokens["admin"]).status_code == 202
        db.expire_all()
        v = db.get(Execucao, velha.id)
        assert v.status == "pausada" and "segue de onde parou" in v.pausa_motivo and v.finalizado_em is not None
        a = db.scalar(select(Auditoria).where(Auditoria.acao == "execucao_interrompida", Auditoria.alvo == f"execucao:{v.id}"))
        assert a is not None and a.detalhe["status_anterior"] == "em_andamento"
    finally:
        _fecha(db)


def test_cc12_completa_viva_parada_vira_pausada(client, tokens, db, lock_de_outro):
    """Tester (#20, ciclo 1): trava OCUPADA + completa sem atividade -> 409 com a execução viva e status inalterado
    (nem pausada, nem cancelar). Só o admin interrompe, por POST /interromper com motivo."""
    ex = _exec(db, tipo=ccomp.TIPO, minutos=30)
    try:
        r = client.post("/api/conectores/saude/amostra", headers=tokens["admin"])
        assert r.status_code == 409 and "carga completa de Saúde em andamento desde" in r.json()["detail"]
        assert client.post("/api/conectores/saude/completa", headers=tokens["admin"]).status_code == 409
        db.expire_all()
        v = db.get(Execucao, ex.id)
        assert v.status == "em_andamento" and not v.cancelar and v.finalizado_em is None
        r = client.post(f"/api/conectores/saude/execucoes/{ex.id}/interromper", headers=tokens["admin"],
                        json={"motivo": "ZZTESTE completa travada"})
        assert r.status_code == 200 and r.json()["status"] == "pausada"  # só pelo admin
    finally:
        _fecha(db)


def test_cc12_completa_parada_trava_livre_vira_pausada(client, tokens, origem, db):  # noqa: F811
    """Trava LIVRE + completa sem atividade: novo pedido pausa a completa (auditado) e a nova carga roda."""
    _amostra(db, "saude")
    ex = _exec(db, tipo=ccomp.TIPO, minutos=30)
    try:
        assert client.post("/api/conectores/saude/amostra", headers=tokens["admin"]).status_code == 202
        db.expire_all()
        v = db.get(Execucao, ex.id)
        assert v.status == "pausada" and v.cancelar is True
        a = db.scalar(select(Auditoria).where(Auditoria.acao == "execucao_interrompida", Auditoria.alvo == f"execucao:{ex.id}"))
        assert a is not None and a.detalhe["automatico"] is True
    finally:
        _fecha(db)


def test_cc11_feitos_igual_total_sem_cpf_sem_dupla_contagem(db):
    """Tester: sem CPF/CNS passam pelas etapas 1 e 3; feitos (sem min) tem que ser exatamente o total."""
    _banco_teste(db)
    ex = Execucao(tipo="zzteste_feitos", sistema="SAUDE", status="sucesso", usuario=USR)
    db.add(ex)
    db.flush()
    sp = db.begin_nested()
    db.execute(text("delete from bronze.registros"))  # banco de TESTE; volta no rollback
    db.add_all([RegistroBronze(sistema_origem="SAUDE", tabela="pacientes", id_origem=f"ZZTESTE{i}",
                               dados={"Matricula": f"ZZTESTE{i}", "Nome": f"ZZTESTE PESSOA {i}", "Cpf": None},
                               hash=f"zzf{i}", execucao_id=ex.id) for i in range(1200)])
    db.flush()
    r = processamento.reconstruir(db)
    assert r["feitos"] == r["total_bronze"] == 1200, r
    sp.rollback()
    db.rollback()


def test_cc11_feitos_igual_total_com_todos_os_papeis(db):
    """TL: no fim do reconstruir, feitos == total (o min() não pode esconder contagem dupla nem registro esquecido)."""
    from app.ingestion import fixture_saude_cemiterio as fx
    from app.ingestion.mapeamentos import MAPEAMENTOS
    _banco_teste(db)
    ex = Execucao(tipo="zzteste_feitos", sistema="SAUDE", status="sucesso", usuario=USR)
    db.add(ex)
    db.flush()
    sp = db.begin_nested()
    db.execute(text("delete from bronze.registros"))  # banco de TESTE (conferido acima); volta no rollback
    dados = {k: v for parte in fx.gerar().values() for k, v in parte.items()}
    papeis = set()
    for (sis, tab), linhas in dados.items():
        if (sis, tab) not in MAPEAMENTOS:
            continue
        papeis.add(MAPEAMENTOS[(sis, tab)].get("papel", "pessoa"))
        for i, r in enumerate(linhas):
            db.add(RegistroBronze(sistema_origem=sis, tabela=tab, id_origem=f"ZZTESTE-{tab}-{i}",
                                  dados={k: (str(v) if v is not None else None) for k, v in r.items()},
                                  hash=f"zz-{tab}-{i}", execucao_id=ex.id))
    db.flush()
    assert {"pessoa", "terceiro", "cns_extra", "revisao", "obito"} <= papeis
    r = processamento.reconstruir(db)
    assert r["feitos"] == r["total_bronze"] > 0, r
    sp.rollback()
    db.rollback()


def test_cc12_completa_parcial_pausada_no_restart_continua_do_checkpoint(client, tokens, origem, db):  # noqa: F811
    """Restart no meio da leitura: completa vira 'pausada' (auditado) e a próxima segue do checkpoint, sem reler
    o que já foi lido e sem duplicar a bronze."""
    from app.models.controle import CheckpointCarga
    from tests.test_zzzz_carga_etapas import _isola_bronze
    _isola_bronze(db, "CEMITERIO")  # banco de TESTE (trava _test): só a bronze desta carga conta
    _amostra(db, "cemiterio")
    o = origem["CEMITERIO"]
    o.paginas, o.falha_na_pagina = 0, 1  # 1ª página gravada; a 2ª "cai" (simula o processo morrendo)
    assert client.post("/api/conectores/cemiterio/completa", headers=tokens["admin"]).status_code == 202
    ex = _ultima(db, "CEMITERIO")
    ex.status, ex.etapa, ex.finalizado_em = "em_andamento", "leitura", None  # como estava quando o servidor parou
    db.commit()
    cp = db.scalar(select(CheckpointCarga).where(CheckpointCarga.sistema == "CEMITERIO", CheckpointCarga.tabela == "Obitos"))
    assert cp.posicao == 2 and not cp.concluida
    ckpt = list(cp.ultima_chave)
    bronze_antes = db.scalar(select(func.count()).select_from(RegistroBronze).where(
        RegistroBronze.sistema_origem == "CEMITERIO", RegistroBronze.tabela == "Obitos"))
    assert ccomp.interromper_orfas(db) == 1  # restart
    db.expire_all()
    v = db.get(Execucao, ex.id)
    assert v.status == "pausada" and "segue de onde parou" in v.pausa_motivo
    assert db.scalar(select(Auditoria).where(Auditoria.acao == "execucao_interrompida", Auditoria.alvo == f"execucao:{ex.id}"))
    s = client.get("/api/conectores/cemiterio/completa", headers=tokens["admin"]).json()
    assert s["execucao"]["status"] == "pausada" and s["execucao"]["tabelas_lidas"] < s["execucao"]["tabelas_total"]
    o.falha_na_pagina, o.paginas = None, 0
    o.sqls.clear()
    assert client.post("/api/conectores/cemiterio/completa", headers=tokens["admin"]).status_code == 202
    ex2 = _ultima(db, "CEMITERIO")
    assert ex2.id != ex.id and ex2.status == "sucesso", ex2.mensagem
    obitos = [p for q, p in o.sqls if "TOP (" in q and "[dbo].[Obitos]" in q]
    assert obitos and list(obitos[0])[-len(ckpt):] == ckpt  # 1ª página da retomada já parte do checkpoint
    ids = list(db.scalars(select(RegistroBronze.id_origem).where(
        RegistroBronze.sistema_origem == "CEMITERIO", RegistroBronze.tabela == "Obitos")))
    assert len(ids) == len(set(ids)) == len(o.linhas("Obitos")) >= bronze_antes  # sem duplicar
    assert all(r["ok"] for r in ccomp.reconciliacao(db, "CEMITERIO").values())
