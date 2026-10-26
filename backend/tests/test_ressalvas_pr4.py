"""Ressalvas baixas do QA do #4: tabelas de apoio, ler_amostra sem política e caracteres invisíveis."""
import pytest

from app.connectors.sqlserver.connector import ConfigSqlServer, SqlServerConnector
from app.connectors.sqlserver.politicas import CEMITERIO, ISS, SAUDE, TabelaRecusada
from app.core.sql_guard import ComandoProibido, garante_somente_leitura


def _con():
    return SqlServerConnector(ConfigSqlServer(None, 1433, None, None, None, 5, 1000))


@pytest.mark.parametrize("pol,tabela", [(SAUDE, t) for t in sorted(SAUDE.apoio)] + [(CEMITERIO, t) for t in sorted(CEMITERIO.apoio)])
def test_apoio_sem_colunas_da_tabela_recusada(pol, tabela):
    with pytest.raises(TabelaRecusada):
        _con().montar_select_politica(pol, tabela)


def test_apoio_continua_autorizado_mas_sem_select():
    SAUDE.autoriza("Aux_Bairro")  # não levanta
    with pytest.raises(TabelaRecusada, match="sem colunas"):
        SAUDE.colunas_select("Aux_Bairro")


def test_ler_amostra_exige_politica():
    with pytest.raises(TypeError):
        _con().ler_amostra("dbo.Municipes")  # type: ignore[call-arg]
    with pytest.raises(TabelaRecusada):
        _con().ler_amostra("dbo.Municipes", politica=None)


INVISIVEIS = ["\u200b", "\u200c", "\u200d", "\u2060", "\ufeff", "\u00ad", "\u202e", "\u2066", "\u180e"]


@pytest.mark.parametrize("ch", INVISIVEIS, ids=[f"U+{ord(c):04X}" for c in INVISIVEIS])
def test_guard_recusa_invisivel(ch):
    with pytest.raises(ComandoProibido):
        garante_somente_leitura(f"SELECT a FROM t WITH (UPD{ch}LOCK)")
    with pytest.raises(ComandoProibido):
        garante_somente_leitura(f"SELECT a{ch} FROM t")
    with pytest.raises(ComandoProibido):
        garante_somente_leitura(f"{ch}SELECT a FROM t")


def test_guard_aceita_select_normal_e_nolock():
    assert garante_somente_leitura("SELECT TOP (10) [a] FROM [dbo].[t] WITH (NOLOCK)")


def test_politica_iss_so_municipes_sem_colunas_sensiveis():
    sql = _con().montar_select_politica(ISS, "Municipes")
    assert sql.startswith("SELECT TOP (1000) [Tipo_Pessoa], [CPF], [Nome]") and "*" not in sql
    for proibida in ("Senha", "CTPS", "Titulo_Eleitor", "Reservista", "CNH"):
        assert proibida not in sql
    for t in ("Contribuintes", "Municipes_Antigo", "Usuarios_Senha", "Municipes_bak"):
        with pytest.raises(TabelaRecusada):
            ISS.colunas_select(t)
