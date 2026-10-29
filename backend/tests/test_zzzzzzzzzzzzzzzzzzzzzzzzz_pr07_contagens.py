"""PR-07 contagens automáticas (J1–J4). Origem 100% falsa (sem SQL Server); dados sintéticos ZZTESTE."""
import pytest
from alembic.script import ScriptDirectory
from sqlalchemy import text

from app.contagens import consultas as C
from app.contagens import job
from app.core.config import get_settings
from app.core.sql_guard import ComandoProibido, garante_somente_leitura
from tests.conftest import alembic_cfg

CPF_REP = "52998224725"  # sintético


class Cursor:
    def __init__(self, conn):
        self.conn, self.rows = conn, []

    def execute(self, sql, params=None):
        self.conn.executados.append(sql)
        self.rows = self.conn.responde(sql)

    def fetchmany(self, n):
        return self.rows[:n]


class Conn:
    def __init__(self, responde, minimo=True):
        self.executados, self._r, self.minimo = [], responde, minimo

    def responde(self, sql):
        if sql == job.SQL_CHECA_LOGIN:
            return [{k: (0 if self.minimo else 1) for k in "abcde"}]
        return self._r(sql)

    def cursor(self, as_dict=True):
        return Cursor(self)

    def rollback(self):
        pass

    def close(self):
        pass


def _q(id_, base="ZZBASE_test", sql="SELECT COUNT(*) total FROM dbo.zz", num=("total",), rot=()):
    return C.Consulta(id_, base, sql, num, rot)


@pytest.fixture
def cenario(monkeypatch):
    monkeypatch.delenv("CONTAGENS_ATIVAS", raising=False)
    monkeypatch.setattr(C, "colunas_cpf", lambda: [("ZZBASE_test", "zz_pessoas", "Cpf")])
    get_settings.cache_clear()
    yield monkeypatch
    get_settings.cache_clear()


def _respostas(sql):
    if "HAVING" in sql:
        return [{"cpf": CPF_REP, "ocorrencias": 60}, {"cpf": "11144477735", "ocorrencias": 49}]
    if "invalidos" in sql:
        return [{"total": 1000, "nulos": 10, "invalidos": 37}]
    return [{"total": 5}]


def test_head_unica_0034(banco):
    """Uma ponta só, e a 0034 está na cadeia (migrações posteriores, como a 0030 do PR-03, podem ficar em cima)."""
    sd = ScriptDirectory.from_config(alembic_cfg())
    heads = sd.get_heads()
    assert len(heads) == 1
    assert "0034" in {r.revision for r in sd.walk_revisions("base", heads[0])}


def test_j1_catalogo_inteiro_passa_no_guard_e_dml_ddl_recusados():
    garante_somente_leitura(job.SQL_CHECA_LOGIN)
    for q in C.CONSULTAS:
        garante_somente_leitura(q.sql)
    for _b, t, c in C.colunas_cpf():
        garante_somente_leitura(C.sql_pct_cpf_invalido(t, c))
        garante_somente_leitura(C.sql_cpf_repetido(t, c, 50, 100))
    for ruim in ("DELETE FROM dbo.zz", "UPDATE dbo.zz SET a=1", "INSERT INTO dbo.zz VALUES (1)", "DROP TABLE dbo.zz",
                 "CREATE TABLE zz (a int)", "ALTER TABLE zz ADD b int", "TRUNCATE TABLE zz", "EXEC sp_who",
                 "SELECT a INTO zz2 FROM zz", "SELECT 1; DELETE FROM zz", "GRANT SELECT ON zz TO x"):
        with pytest.raises(ComandoProibido):
            garante_somente_leitura(ruim)


def test_j1_sql_proibido_nao_chega_na_origem(db, cenario):
    cenario.setattr(C, "CONSULTAS", (_q("ZZ-DML", sql="DELETE FROM dbo.zz"), _q("ZZ-OK")))
    conn = Conn(_respostas)
    ex = job.executar(db, "zzteste@aion.local", conectar=lambda b: conn)
    rows = db.execute(text("select consulta, status, codigo from controle.contagem_resultado where execucao_id=:e"),
                      {"e": ex.id}).all()
    res = {c: (s, k) for c, s, k in rows}
    assert res["ZZ-DML"] == ("falha", "sql_recusado") and res["ZZ-OK"] == ("ok", None)
    assert not any("DELETE" in s for s in conn.executados)
    assert ex.status == "parcial"


def test_j1_login_nao_minimo_recusa_a_base(db, cenario):
    cenario.setattr(C, "CONSULTAS", (_q("ZZ-1"),))
    conn = Conn(_respostas, minimo=False)
    ex = job.executar(db, None, conectar=lambda b: conn)
    codigos = {k for (k,) in db.execute(text("select codigo from controle.contagem_resultado where execucao_id=:e"),
                                        {"e": ex.id})}
    assert codigos == {"login_nao_minimo"}
    assert conn.executados == [job.SQL_CHECA_LOGIN]


def test_j1_sem_login_de_contagens_recusa(db, cenario):
    cenario.setenv("SQLSERVER_HOST", "zz-host")
    cenario.delenv("SQLSERVER_CONTAGENS_USER", raising=False)
    cenario.setenv("SQLSERVER_USER", "zz_login_geral")  # nunca é usado como fallback
    get_settings.cache_clear()
    ex = job.executar(db, None)
    assert (ex.status, ex.codigo) == ("recusada", "login_contagens_ausente")


def test_j2_so_agregados_e_auditoria_por_execucao(db, cenario):
    cenario.setattr(C, "CONSULTAS", (_q("ZZ-NUM"), _q("ZZ-TEXTO", sql="SELECT COUNT(*) total FROM dbo.zt"),
                                     _q("ZZ-EXTRA", sql="SELECT COUNT(*) total FROM dbo.ze"),
                                     _q("ZZ-ROT", sql="SELECT 'PF' tipo, COUNT(*) total FROM dbo.zr", rot=("tipo",))))

    def resp(sql):
        if "dbo.zt" in sql:
            return [{"total": "ZZTESTE FULANO"}]
        if "dbo.ze" in sql:
            return [{"total": 1, "nome": "ZZTESTE FULANO"}]
        if "dbo.zr" in sql:
            return [{"tipo": "PF", "total": 3}]
        return _respostas(sql)
    ex = job.executar(db, "zzteste@aion.local", conectar=lambda b: Conn(resp))
    rows = db.execute(text("select consulta, status, codigo, resultado, sql_hash from controle.contagem_resultado "
                           "where execucao_id=:e"), {"e": ex.id}).all()
    r = {c: (s, k, v, h) for c, s, k, v, h in rows}
    assert r["ZZ-NUM"][:3] == ("ok", None, [{"total": 5}])
    assert r["ZZ-TEXTO"][:3] == ("falha", "resultado_nao_agregado", None)
    assert r["ZZ-EXTRA"][:3] == ("falha", "resultado_nao_agregado", None)
    assert r["ZZ-ROT"][2] == [{"tipo": "PF", "total": 3}]
    assert r["CPF-INV:zz_pessoas"][2] == [{"total": 1000, "nulos": 10, "invalidos": 37}]
    assert all(len(v[3]) == 64 for v in r.values())
    tudo = db.execute(text("select resultado::text from controle.contagem_resultado where execucao_id=:e"),
                      {"e": ex.id}).all()
    assert not any("ZZTESTE" in (t or "") for (t,) in tudo)
    aud = db.execute(text("select detalhe from controle.auditoria where acao='contagens_execucao' and alvo=:a"),
                     {"a": f"contagem:{ex.id}"}).all()
    assert len(aud) == 1 and aud[0][0]["consultas"] == len(rows) and aud[0][0]["sql"][0]["sql_hash"]


def test_j2_consultas_postgres_rodam_read_only(db, cenario):
    cenario.setattr(C, "CONSULTAS", tuple(q for q in C.CONSULTAS if q.base == "postgres"))
    cenario.setattr(C, "colunas_cpf", lambda: [])
    ex = job.executar(db, None, conectar=lambda b: pytest.fail("não deveria abrir SQL Server"))
    st = dict(db.execute(text("select consulta, status from controle.contagem_resultado where execucao_id=:e"),
                         {"e": ex.id}).all())
    assert st == {"Q-A6": "ok", "Q-A7": "ok", "Q-A8": "ok", "Q-A9": "ok"}
    with pytest.raises(Exception):
        job._pg(db, "SELECT count(*) FROM (SELECT 1) q WHERE pg_catalog.nextval('zz') > 0")


def test_j3_base_inacessivel_codigo_fixo_e_segue(db, cenario):
    cenario.setattr(C, "CONSULTAS", (_q("ZZ-A", base="ZZ_INACESSIVEL_test"), _q("ZZ-B", base="ZZBASE_test")))

    def conectar(base):
        if base == "ZZ_INACESSIVEL_test":
            raise RuntimeError("login falhou para zz_usuario senha=zz-segredo host=10.0.0.1")
        return Conn(_respostas)
    ex = job.executar(db, None, conectar=conectar)
    r = dict(db.execute(text("select consulta, codigo from controle.contagem_resultado where execucao_id=:e"),
                        {"e": ex.id}).all())
    assert r["ZZ-A"] == "base_inacessivel" and r["ZZ-B"] is None
    tudo = db.execute(text("select coalesce(codigo,'') || coalesce(resultado::text,'') from controle.contagem_resultado "
                           "where execucao_id=:e union all select detalhe::text from controle.auditoria where alvo=:a"),
                      {"e": ex.id, "a": f"contagem:{ex.id}"}).all()
    assert not any("segredo" in t or "10.0.0.1" in t for (t,) in tudo)


def test_j4_so_cpfs_acima_do_limiar_e_configuravel(db, cenario):
    cenario.setattr(C, "CONSULTAS", ())
    sqls = []

    def resp(sql):
        sqls.append(sql)
        return _respostas(sql)
    ex = job.executar(db, None, conectar=lambda b: Conn(resp))
    cpfs = db.execute(text("select cpf, ocorrencias, limiar from controle.contagem_cpf_repetido where execucao_id=:e"),
                      {"e": ex.id}).all()
    assert cpfs == [(CPF_REP, 60, 50)]
    assert any("HAVING COUNT(*) >= 50" in s for s in sqls)
    cenario.setenv("CONTAGENS_CPF_REPETIDO_LIMIAR", "70")
    get_settings.cache_clear()
    ex2 = job.executar(db, None, conectar=lambda b: Conn(resp))
    assert ex2.limiar_cpf == 70
    assert db.execute(text("select count(*) from controle.contagem_cpf_repetido where execucao_id=:e"),
                      {"e": ex2.id}).scalar() == 0
    assert any("HAVING COUNT(*) >= 70" in s for s in sqls)


def test_j4_banco_recusa_cpf_abaixo_do_limiar(db, cenario):
    from sqlalchemy.exc import IntegrityError
    ex = job.executar(db, None, conectar=lambda b: Conn(_respostas))
    with pytest.raises(IntegrityError):
        db.execute(text("insert into controle.contagem_cpf_repetido (execucao_id, base, tabela, cpf, ocorrencias, limiar) "
                        "values (:e,'ZZ','zz','11144477735',49,50)"), {"e": ex.id})
    db.rollback()


def test_j4_lista_so_admin_lgpd_e_auditada(client, tokens, db, cenario):
    ex = job.executar(db, None, conectar=lambda b: Conn(_respostas))
    url = f"/api/contagens/{ex.id}/cpfs-repetidos"
    for perfil in ("admin", "operador", "leitura"):
        assert client.get(url, headers=tokens[perfil]).status_code == 403
    antes = db.execute(text("select count(*) from controle.auditoria where acao='contagens_cpf_repetido_leitura'")).scalar()
    r = client.get(url, headers=tokens["admin_lgpd"])
    assert r.status_code == 200 and r.json()["itens"][0]["cpf"] == CPF_REP and r.json()["limiar"] == 50
    db.expire_all()
    depois = db.execute(text("select count(*) from controle.auditoria where acao='contagens_cpf_repetido_leitura'")).scalar()
    assert depois == antes + 1
    ult = client.get("/api/contagens/ultima", headers=tokens["admin"])
    assert ult.status_code == 200 and CPF_REP not in ult.text
    assert client.get("/api/contagens/ultima", headers=tokens["leitura"]).status_code == 403
    assert client.post("/api/contagens/executar", headers=tokens["operador"]).status_code == 403


def test_marcadas_ajustar_so_rodam_liberadas(monkeypatch):
    monkeypatch.delenv("CONTAGENS_ATIVAS", raising=False)
    padrao = {q.id for q in job.selecionadas()}
    assert "Q1" not in padrao and "Q-A2" in padrao
    monkeypatch.setenv("CONTAGENS_ATIVAS", "Q1")
    assert [q.id for q in job.selecionadas()] == ["Q1"]


def test_q2_q3_q4_removidas_geral_nunca_lido():
    ids = {q.id for q in C.CONSULTAS}
    assert not ids & {"Q2", "Q3", "Q4"}
    assert not any("Geral." in q.sql or q.base == "Geral" for q in C.CONSULTAS)


def test_agenda_contagens_0545_nasce_desligada(db):
    r = db.execute(text("select horario, ativo from controle.agendamentos where conector='contagens'")).one()
    assert r == ("05:45", False)


def test_agenda_roda_contagens_as_0545_quando_ligada(db, cenario):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from app.services import agenda
    cenario.setattr(C, "CONSULTAS", (_q("ZZ-AG"),))
    cenario.setattr(job, "_conector_sqlserver", lambda base: Conn(_respostas))
    cenario.setenv("SQLSERVER_HOST", "zz-host")
    cenario.setenv("SQLSERVER_CONTAGENS_USER", "zz_contagens")
    get_settings.cache_clear()
    antes = db.execute(text("select conector, ativo from controle.agendamentos")).all()
    db.execute(text("update controle.agendamentos set ativo = (conector='contagens')"))
    db.execute(text("update controle.agendamentos set ultima_data=null, tentativas=0, proxima_tentativa_em=null "
                    "where conector='contagens'"))
    db.commit()
    try:
        cedo = datetime(2026, 10, 30, 5, 30, tzinfo=ZoneInfo("America/Sao_Paulo"))
        assert ("contagens", "sucesso") not in agenda.ciclo(db, agora=cedo)
        hora = datetime(2026, 10, 30, 5, 46, tzinfo=ZoneInfo("America/Sao_Paulo"))
        feitos = agenda.ciclo(db, agora=hora)
        assert ("contagens", "sucesso") in feitos
        assert agenda._nome("contagens") == agenda.NOME_CONTAGENS
    finally:
        for conector, ativo in antes:
            db.execute(text("update controle.agendamentos set ativo=:a where conector=:c"), {"a": ativo, "c": conector})
        db.commit()


def test_j4a_piso_50_travado(db, cenario, caplog):
    cenario.setattr(C, "CONSULTAS", ())
    cenario.setenv("CONTAGENS_CPF_REPETIDO_LIMIAR", "10")
    get_settings.cache_clear()
    sqls = []

    def resp(sql):
        sqls.append(sql)
        return [{"cpf": CPF_REP, "ocorrencias": 20}] if "HAVING" in sql else _respostas(sql)
    with caplog.at_level("ERROR", logger="sigpmf.contagens"):
        ex = job.executar(db, None, conectar=lambda b: Conn(resp))
    assert ex.limiar_cpf == 50 and any("HAVING COUNT(*) >= 50" in s for s in sqls)
    assert any("piso" in r.getMessage() for r in caplog.records)
    assert db.execute(text("select count(*) from controle.contagem_cpf_repetido where execucao_id=:e"),
                      {"e": ex.id}).scalar() == 0


def test_j4b_so_ultima_execucao_guarda_cpf_e_30_dias(db, cenario):
    from datetime import datetime, timedelta, timezone
    cenario.setattr(C, "CONSULTAS", ())
    ex1 = job.executar(db, None, conectar=lambda b: Conn(_respostas))
    ex2 = job.executar(db, None, conectar=lambda b: Conn(_respostas))
    r1 = db.execute(text("select cpf, ocorrencias, expurgado_em from controle.contagem_cpf_repetido where execucao_id=:e"),
                    {"e": ex1.id}).all()
    assert r1 and all(c is None and o == 60 and x is not None for c, o, x in r1)  # histórico: só totais
    assert db.execute(text("select count(*) from controle.contagem_cpf_repetido where cpf is not null "
                           "and execucao_id <> :e"), {"e": ex2.id}).scalar() == 0
    db.execute(text("update controle.contagem_cpf_repetido set registrado_em = now() - interval '31 days' "
                    "where execucao_id=:e"), {"e": ex2.id})
    db.commit()
    job.expurga_cpfs(db, ex2.id, datetime.now(timezone.utc))
    assert db.execute(text("select count(*) from controle.contagem_cpf_repetido where cpf is not null")).scalar() == 0
    assert db.execute(text("select count(*) from controle.contagem_cpf_repetido where execucao_id=:e"),
                      {"e": ex2.id}).scalar() == 1  # sem exclusão física
    assert timedelta(days=job.RETENCAO_CPF_DIAS) == timedelta(days=30)
