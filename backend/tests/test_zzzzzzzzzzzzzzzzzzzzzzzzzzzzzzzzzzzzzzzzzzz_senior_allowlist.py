"""Senior onda HE: DBSENIOR entra na lista permitida (só contagens, login só-leitura); DBSENIOR_PORTAL segue descartada.
Origem 100% falsa (sem SQL Server); só agregados sintéticos."""
import pytest
from sqlalchemy import text

from app.connectors.sqlserver import politicas as P
from app.contagens import consultas as C
from app.contagens import job
from app.core.config import get_settings
from tests.test_zzzzzzzzzzzzzzzzzzzzzzzzz_pr07_contagens import Conn


def test_dbsenior_permitida_e_canonica():
    assert "DBSENIOR" in P.BASES_PERMITIDAS
    for nome in ("DBSENIOR", "dbsenior", "[DBSENIOR]", "ＤＢＳＥＮＩＯＲ"):
        assert P.autoriza_base(nome) == "DBSENIOR"


@pytest.mark.parametrize("nome", ["DBSENIOR_PORTAL", "dbsenior_portal", "[DBSENIOR_PORTAL]"])
def test_dbsenior_portal_continua_descartada(nome):
    assert "DBSENIOR_PORTAL" in P.BASES_DESCARTE and "DBSENIOR_PORTAL" not in P.BASES_PERMITIDAS
    with pytest.raises(P.BaseRecusada) as e:
        P.autoriza_base(nome)
    assert e.value.codigo == "base_descartada"


@pytest.mark.parametrize("nome", ["DBSENIOR_EDOCS", "DBSENIOR_ESOCIAL"])
def test_outras_bases_senior_seguem_fora(nome):
    with pytest.raises(P.BaseRecusada) as e:
        P.autoriza_base(nome)
    assert e.value.codigo == "base_fora_da_lista"


def test_config_senior_le_so_nome_do_banco(monkeypatch):
    monkeypatch.setenv("SQLSERVER_DATABASE_SENIOR", "DBSENIOR")
    get_settings.cache_clear()
    try:
        assert get_settings().sqlserver_database_senior == "DBSENIOR"
    finally:
        get_settings.cache_clear()


def test_q10_deixa_de_ser_pulada(db, monkeypatch):
    q10 = next(q for q in C.CONSULTAS if q.id == "Q10")
    assert q10.base == "DBSENIOR" and not q10.ajustar
    monkeypatch.delenv("CONTAGENS_ATIVAS", raising=False)
    monkeypatch.setattr(C, "CONSULTAS", (q10,))
    monkeypatch.setattr(C, "colunas_cpf", lambda: [])
    bases = []
    conn = Conn(lambda sql: [{"servidores": 100, "sem_cpf": 0, "cpf_menos_11_digitos": 7}])

    def conectar(base):
        bases.append(base)
        return conn
    ex = job.executar(db, "zzteste@aion.local", conectar=conectar)
    st, cod, res = db.execute(text("select status, codigo, resultado from controle.contagem_resultado "
                                   "where execucao_id=:e and consulta='Q10'"), {"e": ex.id}).one()
    assert (st, cod) == ("ok", None)
    assert res == [{"servidores": 100, "sem_cpf": 0, "cpf_menos_11_digitos": 7}]
    assert bases == ["DBSENIOR"]
    assert conn.executados[0] == job.SQL_CHECA_LOGIN and any("r034fun" in s for s in conn.executados)
