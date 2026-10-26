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


def test_cc12_viva_sem_atividade_alem_do_limite_vira_interrompida(client, tokens, db, lock_de_outro):
    ex = _exec(db, minutos=30)  # SIGPMF_CARGA_ALERTA_MIN padrão 5
    try:
        r = client.post("/api/conectores/saude/amostra", headers=tokens["admin"])
        assert r.status_code == 409 and "marcada como interrompida" in r.json()["detail"]
        db.expire_all()
        v = db.get(Execucao, ex.id)
        assert v.status == "interrompida" and v.cancelar is True
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
