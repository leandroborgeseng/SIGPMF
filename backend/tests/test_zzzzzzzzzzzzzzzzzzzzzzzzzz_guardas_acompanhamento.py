"""Acompanhamento das guardas: R1/R2 do #49, R1 do #51 (helper), ressalvas do #52 e do #47.
Só nomes e dados sintéticos; nenhuma conexão real (driver sempre mockado)."""
import logging
import sys
import types
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.connectors.sqlserver import guardas as G
from app.connectors.sqlserver import politicas as P
from app.connectors.sqlserver.politicas import BaseRecusada
from app.core import permissoes_origem as PO
from app.models.controle import Agendamento
from app.services import agenda as ag

ZW = "\u200b"


# ---------- R1 do #49: lista permitida com nome normalizado ----------
@pytest.mark.parametrize("nome", ["[Senhas ]", '"Senhas"', "Senhas;", f"Senhas{ZW}", "Ｓｅｎｈａｓ",
                                  f"[{ZW}Senhas]", " senhas ", "SENHAS", "'Internet'", "Internet\ufeff"])
def test_r1_descarte_com_decoracao_recusado(nome):
    with pytest.raises(BaseRecusada) as e:
        P.autoriza_base(nome)
    assert e.value.codigo == "base_descartada"


@pytest.mark.parametrize("nome", ["ISS", "iss", "[ISS]", "FRA_CoreSSO", "fra_gestaoescolar", "API SIL", "Correios"])
def test_r1_permitidas_aceitas(nome):
    P.autoriza_base(nome)


@pytest.mark.parametrize("nome", ["[ISS ]", '"ISS"', "ISS;", f"ISS{ZW}", "ＩＳＳ", "[[ISS]]", "ISS\u2060"])
def test_r1_permitida_com_decoracao_recusada(nome):
    with pytest.raises(BaseRecusada) as e:
        P.autoriza_base(nome)
    assert e.value.codigo == "base_nome_invalido"


@pytest.mark.parametrize("nome", ["Geral", "ManutHardware", "master", "tempdb", "Folpag_", "BaseFicticia", "", "zz_test;x"])
def test_r1_fora_da_lista_recusada(nome):
    with pytest.raises(BaseRecusada) as e:
        P.autoriza_base(nome)
    assert e.value.codigo in ("base_fora_da_lista", "base_nome_invalido")


def test_r1_banco_test_so_em_app_env_test(monkeypatch):
    P.autoriza_base("zz_sintetico_test")  # conftest: APP_ENV=test
    monkeypatch.setattr(P, "_ambiente_teste", lambda: False)
    with pytest.raises(BaseRecusada) as e:
        P.autoriza_base("zz_sintetico_test")
    assert e.value.codigo == "base_fora_da_lista"


def test_r1_normaliza_base():
    assert P.normaliza_base(f' [ "Ｓｅｎｈａｓ{ZW}" ]; ') == "senhas"
    assert P.normaliza_base("API SIL") == "api sil"


def test_r1_todas_as_politicas_e_bancos_padrao_estao_na_lista():
    from app.core.config import get_settings
    s = get_settings()
    for pol in P.POLITICAS.values():
        P.autoriza_base(pol.base)
    P.autoriza_base(s.ge_banco_escolar)
    P.autoriza_base(s.ge_banco_pessoas)
    assert not {P.normaliza_base(b) for b in P.BASES_PERMITIDAS} & {P.normaliza_base(b) for b in P.BASES_NUNCA}


# ---------- R2 do #49: LeitorGE chama a guarda antes de abrir conexão ----------
def _cfg_ge(escolar="FRA_GestaoEscolar", pessoas="FRA_CoreSSO"):
    from app.connectors.gestaoescolar import consultas as cq
    return cq.ConfigGE("zz-host-teste", 1433, "zz_leitor_teste", "zz-senha-teste", escolar, pessoas, 10, 1, 50)


@pytest.mark.parametrize("escolar,pessoas", [("Senhas", "FRA_CoreSSO"), ("FRA_GestaoEscolar", "Internet"),
                                             ("FRA_GestaoEscolar", "Geral")])
def test_r2_leitor_ge_nao_abre_conexao_em_base_recusada(monkeypatch, escolar, pessoas):
    from app.connectors.gestaoescolar import consultas as cq
    chamado = []
    monkeypatch.setitem(sys.modules, "pymssql", types.SimpleNamespace(connect=lambda **k: chamado.append(k)))
    try:
        cfg = _cfg_ge(escolar, pessoas)
    except Exception:  # noqa: BLE001 - a própria validação da config já recusou: também não conecta
        assert chamado == []
        return
    with pytest.raises(BaseRecusada):
        with cq.LeitorGE(cfg):
            pass
    assert chamado == []


def test_r2_leitor_ge_conecta_em_base_permitida(monkeypatch):
    from app.connectors.gestaoescolar import consultas as cq
    chamado = []
    conn = types.SimpleNamespace(close=lambda: None)
    monkeypatch.setitem(sys.modules, "pymssql", types.SimpleNamespace(connect=lambda **k: chamado.append(k) or conn))
    with cq.LeitorGE(_cfg_ge()) as lt:
        assert lt._conn is conn
    assert len(chamado) == 1 and chamado[0]["database"] == "FRA_GestaoEscolar"


def test_conecta_guardado_recusa_antes_do_driver():
    chamado = []
    with pytest.raises(BaseRecusada):
        G.conecta_guardado(lambda **k: chamado.append(k), "ISS", "Senhas", server="zz")
    with pytest.raises(BaseRecusada):
        G.conecta_guardado(lambda **k: chamado.append(k), "Telefonia")
    assert chamado == []
    assert G.conecta_guardado(lambda **k: k, "Divida", server="zz") == {"database": "Divida", "server": "zz"}


# ---------- R1 do #51: permissão de escrita por fn_my_permissions ----------
def _executor(banco, objetos=None):
    chamadas = []

    def ex(conn, sql, params=None, *, limite):
        chamadas.append((sql, params))
        if "'DATABASE'" in sql:
            return [{"permission_name": p} for p in banco]
        return [(p,) for p in (objetos or {}).get(params[0], [])]
    return ex, chamadas


def test_r1_51_somente_leitura_ok():
    ex, ch = _executor(["CONNECT", "SELECT", "VIEW DEFINITION", "SHOWPLAN"])
    assert PO.permissoes_de_escrita(None, executor=ex) == []
    PO.garante_sem_escrita(None, executor=ex)
    assert ch[0][0] == PO.SQL_PERMISSOES_BANCO


@pytest.mark.parametrize("origem,perms", [
    ("papel herdado db_datawriter", ["CONNECT", "SELECT", "INSERT", "UPDATE", "DELETE"]),
    ("public com GRANT", ["CONNECT", "SELECT", "insert "]),
    ("papel customizado com DDL", ["CONNECT", "SELECT", "ALTER", "CREATE TABLE"]),
    ("db_owner", ["CONTROL", "SELECT"]),
])
def test_r1_51_escrita_efetiva_recusada(origem, perms):
    ex, _ = _executor(perms)
    with pytest.raises(PO.CredencialComEscrita):
        PO.garante_sem_escrita(None, executor=ex)


def test_r1_51_escrita_em_objeto_por_grant_direto():
    ex, ch = _executor(["CONNECT", "SELECT"], {"dbo.ZZ_Tabela": ["SELECT", "UPDATE"]})
    assert PO.permissoes_de_escrita(None, ["dbo.ZZ_Tabela"], executor=ex) == ["dbo.ZZ_Tabela:UPDATE"]
    assert ch[1] == (PO.SQL_PERMISSOES_OBJETO, ("dbo.ZZ_Tabela",))


def test_r1_51_sql_passa_pelo_guard():
    from app.core.sql_guard import garante_somente_leitura
    garante_somente_leitura(PO.SQL_PERMISSOES_BANCO)
    garante_somente_leitura(PO.SQL_PERMISSOES_OBJETO)


# ---------- ressalvas do #52 ----------
def test_52a_painel_educacao_proximo_modo_incremental(db):
    item = db.scalar(select(Agendamento).where(Agendamento.conector == ag.EDUCACAO))
    assert item is not None and item.ultima_completa_em is None
    est = ag.estado(db, datetime(2026, 10, 29, 12, 0, tzinfo=UTC))
    edu = next(i for i in est["itens"] if i["conector"] == ag.EDUCACAO)
    assert edu["proximo_modo"] == "incremental"


def test_52b_docstring_sem_frase_errada():
    import app.services.agenda as mod
    assert "nunca faz completa" not in mod.__doc__
    assert "sempre COMPLETA" in mod.__doc__


def test_52c_parcial_repetido_avisa(db, monkeypatch, caplog):
    item = db.scalar(select(Agendamento).where(Agendamento.conector == "sil"))
    antes = (item.ultima_data, item.tentativas, item.proxima_tentativa_em, item.ultimo_status, item.ultima_mensagem)
    alertas = []
    monkeypatch.setattr(ag, "alertar", lambda *a, **k: alertas.append(a))
    caplog.set_level(logging.WARNING, logger="sigpmf.agenda")
    agora = datetime(2026, 10, 29, 4, 0, tzinfo=UTC)
    try:
        item.ultimo_status = "sucesso"
        ag._encerra(db, item, "parcial", "zz parcial", agora, resolvido=True)
        assert alertas == [] and "agenda_parcial_repetida" not in caplog.text
        ag._encerra(db, item, "parcial", "zz parcial", agora, resolvido=True)
        assert len(alertas) == 1 and "agenda_parcial_repetida conector=sil" in caplog.text
    finally:
        item.ultima_data, item.tentativas, item.proxima_tentativa_em, item.ultimo_status, item.ultima_mensagem = antes
        db.commit()


# ---------- ressalva do #47: Id_Logradouro nulo nunca vence o desempate ----------
def test_47_id_nulo_nao_vence_desempate():
    from app.services import dne
    nulo, um, dez = {"id_logradouro": None}, {"id_logradouro": 1}, {"id_logradouro": 10}
    assert sorted([nulo, dez, um], key=dne._chave_desempate) == [um, dez, nulo]
    assert dne._chave_desempate(um) < dne._chave_desempate(nulo)


# ---------- #51 ligado às guardas: contagens ----------
def _contagens_cenario(monkeypatch, consultas, perms=()):
    from app.contagens import consultas as C
    from app.contagens import job
    from app.core.config import get_settings
    monkeypatch.delenv("CONTAGENS_ATIVAS", raising=False)
    monkeypatch.setattr(C, "CONSULTAS", consultas)
    monkeypatch.setattr(C, "colunas_cpf", lambda: [])
    get_settings.cache_clear()
    abertas, executados = [], []

    class Cur:
        def execute(self, sql, params=None):
            executados.append(sql)
            if sql == job.SQL_CHECA_LOGIN:
                self.rows = [{k: 0 for k in "abcde"}]
            elif "fn_my_permissions" in sql:
                self.rows = [{"permission_name": p} for p in ("CONNECT", "SELECT", *perms)]
            else:
                self.rows = [{"total": 5}]

        def fetchmany(self, n):
            return self.rows[:n]

    class Conn:
        def cursor(self, as_dict=True):
            return Cur()

        def rollback(self):
            pass

        def close(self):
            pass

    def conectar(base):
        abertas.append(base)
        return Conn()
    return job, conectar, abertas, executados


def _resultados(db, ex_id):
    from sqlalchemy import text
    return {c: (s, k) for c, s, k in db.execute(text(
        "select consulta, status, codigo from controle.contagem_resultado where execucao_id=:e"), {"e": ex_id})}


def test_51_consulta_em_banco_fora_da_lista_fica_desligada_sem_conexao(db, monkeypatch, caplog):
    from app.contagens.consultas import Consulta
    caplog.set_level(logging.INFO, logger="sigpmf.contagens")
    qs = (Consulta("ZZ-FORA", "Central", "SELECT COUNT(*) total FROM dbo.zz", ("total",), ()),
          Consulta("ZZ-DESC", "Senhas", "SELECT COUNT(*) total FROM dbo.zz", ("total",), ()),
          Consulta("ZZ-OK", "zz_contagens_test", "SELECT COUNT(*) total FROM dbo.zz", ("total",), ()))
    job, conectar, abertas, _ = _contagens_cenario(monkeypatch, qs)
    ex = job.executar(db, None, conectar=conectar)
    r = _resultados(db, ex.id)
    assert r["ZZ-FORA"] == ("desligada", "base_fora_da_lista")
    assert r["ZZ-DESC"] == ("desligada", "base_descartada")
    assert r["ZZ-OK"] == ("ok", None)
    assert abertas == ["zz_contagens_test"]  # nunca conectou em Central nem Senhas
    assert ex.status == "concluida"  # desligada não é falha


@pytest.mark.parametrize("perm", ["INSERT", "UPDATE", "DELETE", "ALTER", "CONTROL", "CREATE TABLE"])
def test_51_login_com_qualquer_escrita_recusa_a_base(db, monkeypatch, perm):
    from app.contagens.consultas import Consulta
    qs = (Consulta("ZZ-1", "zz_contagens_test", "SELECT COUNT(*) total FROM dbo.zz", ("total",), ()),)
    job, conectar, abertas, executados = _contagens_cenario(monkeypatch, qs, perms=(perm,))
    ex = job.executar(db, None, conectar=conectar)
    assert _resultados(db, ex.id)["ZZ-1"] == ("falha", "login_com_escrita")
    assert not any("dbo.zz" in s for s in executados)  # a contagem não rodou
    assert any("fn_my_permissions" in s for s in executados)


def test_51_conector_real_passa_por_conecta_guardado(monkeypatch):
    from app.contagens import job
    chamado = []
    monkeypatch.setitem(sys.modules, "pymssql", types.SimpleNamespace(connect=lambda **k: chamado.append(k) or "c"))
    with pytest.raises(BaseRecusada):
        job._conector_sqlserver("Senhas")
    with pytest.raises(BaseRecusada):
        job._conector_sqlserver("Central")
    assert chamado == []
    assert job._conector_sqlserver("IPTU") == "c" and chamado[0]["database"] == "IPTU"
