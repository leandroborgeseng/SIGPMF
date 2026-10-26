"""Ressalvas do Tester no #6 (Munícipe) + CC9. Só dados sintéticos gerados aqui."""
import pytest
from sqlalchemy import text

from app.connectors.sqlserver.politicas import ISS, TabelaRecusada, coluna_proibida, filtra_colunas
from app.ingestion import municipe as mun
from app.models.camadas import RegistroBronze
from tests.test_municipe import cpf, linha


def _n(r, motivo):
    return [p for p in r.pendencias if p["motivo"] == motivo]


# ---------- Ressalva 1: Tipo_Pessoa antes da deduplicação ----------
@pytest.mark.parametrize("tipo", ["R", None, "X"])
def test_r1_tipo_indefinido_repetido_nao_some(tipo):
    d = cpf(700000001)
    linhas = [linha(d, tipo=tipo, nome="REP V1", quando="2026-01-01"),
              linha(d, tipo=tipo, nome="REP V2", quando="2026-02-01"),
              linha(d, tipo=tipo, nome="REP V1", quando="2026-01-01")]  # idêntica à 1ª
    r = mun.classificar(linhas)
    p = _n(r, "tipo_pessoa_indefinido")
    assert len(p) == 3 and r.contagem["duplicadas"] == 0 and r.contagem["pendentes"] == 3
    assert len({x["chave"] for x in p}) == 3          # nenhuma colapsa (nem a idêntica)
    assert not r.registros and r.fecha()


def test_r1_documento_invalido_repetido_vira_pendencia_por_linha():
    d = cpf(700000002)
    ruim = d[:9] + str((int(d[9]) + 1) % 10) + d[10]
    r = mun.classificar([linha(ruim, quando="2026-01-01"), linha(ruim, quando="2026-02-01")])
    assert len(_n(r, "documento_invalido")) == 2 and r.contagem["duplicadas"] == 0 and r.fecha()


def test_r1_fisica_valida_continua_deduplicando():
    d = cpf(700000003)
    r = mun.classificar([linha(d, quando="2026-01-01", nome="V1"), linha(d, quando="2026-02-01", nome="V2")])
    assert r.contagem["duplicadas"] == 1 and r.registros[0].dados["nome"] == "V2" and r.fecha()


def test_r1_carga_grava_todas_as_pendencias_r(client, db):
    from app.models.camadas import MunicipePendencia
    from app.services.carga_municipe import executar_carga_municipe
    d = cpf(700000004)
    antes = db.query(MunicipePendencia).count()
    ex = executar_carga_municipe(db, "operador@aion.local", linhas=[
        linha(d, tipo="R", nome="R UM", quando="2026-01-01"), linha(d, tipo="R", nome="R DOIS", quando="2026-03-01")])
    assert ex.pendentes == 2 and ex.duplicados == 0
    assert db.query(MunicipePendencia).count() - antes == 2


def test_m3_juridica_em_bronze_e_silver_sem_cidadao(client, db):
    from app.models.camadas import Municipe
    from app.services.carga_municipe import executar_carga_municipe
    from tests.test_municipe import cnpj
    j = cnpj(77000001)
    executar_carga_municipe(db, "operador@aion.local", linhas=[linha(j, tipo="J", nome="EMPRESA SINTETICA X")],
                            bronze=True)
    m = db.get(Municipe, j)
    assert m is not None and m.tipo == "empresa" and m.cidadao_id is None
    bronze = [b for b in db.query(RegistroBronze).filter_by(sistema_origem="ISS", tabela="Municipes")
              if b.dados.get("Nome") == "EMPRESA SINTETICA X"]
    assert len(bronze) == 1


# ---------- Ressalva 2: colunas proibidas por padrão ----------
@pytest.mark.parametrize("nome", ["Senha", "SENHA", "senha_web", "SenhaAntiga", "Visualiz_Senha", "UserPassword",
                                  "PASSWORD_HASH", "remember_token", "ApiToken", "CID", "Cod_CID", "CID10", "cid_10",
                                  "Renda", "RendaFamiliar", "renda_per_capita", "CTPS", "Título_Eleitor", "Reservista",
                                  "CNH", "Raça", "Cor", "Secret_Key", "Deficiência", "secret",
                                  "client_secret", "ClientSecret", "pwd", "user_pwd", "UserPwd", "renda",
                                  "renda_familiar", "RENDA-MENSAL", "secretkey", "SECRETKEY_V2",
                                  "oauthclientsecret", "Xapisecret", "userpwd", "olduserpwdhash", "CampoSecretKey"])
def test_r2_detecta_variantes(nome):
    assert coluna_proibida(nome)


@pytest.mark.parametrize("nome", ["Cidade", "Cidadao", "Nome", "Nome_Mae", "Data_Alteracao", "Pis_Pasep", "CPF",
                                  "Documento_SoNumeros", "Corpo", "Codigo", "Tipo_Pessoa", "Data_Nascimento",
                                  "Secretaria", "Cod_Secretaria", "SecretariaOrigem", "Secretário", "Prenda",
                                  "Rendimento_Escolar"])
def test_r2_nao_pega_colunas_legitimas(nome):
    assert not coluna_proibida(nome)


def test_r2_politica_recusa_coluna_variante():
    from dataclasses import replace
    pol = replace(ISS, colunas={"Municipes": ISS.colunas["Municipes"] + ("Senha_Web",)})
    with pytest.raises(TabelaRecusada):
        pol.colunas_select("Municipes")
    pol = replace(ISS, colunas={"Municipes": ("Nome", "título-eleitor")})
    with pytest.raises(TabelaRecusada):
        pol.colunas_select("Municipes")


def test_r2_nenhuma_politica_atual_tem_coluna_proibida():
    from app.connectors.sqlserver.politicas import POLITICAS
    for p in POLITICAS.values():
        for t in p.colunas:
            p.colunas_select(t)


def test_r2_coluna_proibida_nunca_chega_a_bronze(client, db):
    from app.services.carga_municipe import executar_carga_municipe
    d = cpf(700000005)
    extra = {"senha": "x1", "Password": "x2", "API_TOKEN": "x3", "CID10": "x4", "RendaFamiliar": "x5", "Coluna_Nova": "x6"}
    executar_carga_municipe(db, "operador@aion.local", linhas=[{**linha(d, nome="BRONZE LIMPA"), **extra}], bronze=True)
    reg = [b for b in db.query(RegistroBronze).filter_by(sistema_origem="ISS", tabela="Municipes")
           if b.dados.get("Nome") == "BRONZE LIMPA"]
    assert len(reg) == 1
    assert not set(extra) & set(reg[0].dados)
    assert filtra_colunas({"Nome": "N", "Senha": "s"}, ("Nome", "Senha")) == {"Nome": "N"}


# ---------- Ressalva 3: pendências mascaradas fora do admin_lgpd ----------
def test_r3_pendencias_nome_mascarado(client, db, tokens):
    from app.services.carga_municipe import executar_carga_municipe
    executar_carga_municipe(db, "operador@aion.local", linhas=[linha(cpf(700000006), tipo="R", nome="NOME SECRETO SINTETICO")])
    for perfil in ("operador", "admin"):
        r = client.get("/api/silver/municipes/pendencias", headers=tokens[perfil])
        assert r.status_code == 200 and "NOME SECRETO" not in r.text and "SECRETO" not in r.text
        assert any(x["nome"] == "N*** S****** S********" for x in r.json())
        assert cpf(700000006) not in r.text
    r = client.get("/api/silver/municipes/pendencias", headers=tokens["admin_lgpd"])
    assert r.status_code == 200 and "NOME SECRETO SINTETICO" in r.text and cpf(700000006) not in r.text
    assert client.get("/api/silver/municipes/pendencias", headers=tokens["leitura"]).status_code == 403


# ---------- CC9: chave nula -> quarentena na bronze ----------
def _sonum(d, **kw):
    return {**linha(d, **kw), "Documento_SoNumeros": d}


def _rec(db):
    from app.services.carga_municipe import reconciliacao
    return reconciliacao(db, 0)


def test_cc9_quarentena_reconcilia_e_ok_com_pendencias(client, db):
    from app.services.carga_municipe import executar_carga_municipe, reconciliacao
    a = _rec(db)
    base = a["bronze"] + a["quarentena"]
    boas = [_sonum(cpf(710000000 + i), nome=f"CC9 BOA {i}") for i in range(199)]
    nula = {**linha(None, nome="CC9 NULA"), "Documento_SoNumeros": None}
    vazia = {**linha("  ", nome="CC9 VAZIA"), "Documento_SoNumeros": " "}
    ex = executar_carga_municipe(db, "operador@aion.local", linhas=boas + [nula], nulos=[nula, vazia],
                                 total_origem=base + 201, total_nulos=2, bronze=True)
    assert ex.status != "falha", ex.mensagem
    assert "2 linha(s) em quarentena (chave nula)" in ex.mensagem and "ignorad" not in ex.mensagem
    rec = reconciliacao(db, base + 201)
    assert rec["quarentena"] - a["quarentena"] == 2 and rec["bronze"] - a["bronze"] == 199
    assert rec["ok"] and rec["status"] == "ok_com_pendencias"
    assert "status=amostra" in ex.mensagem and "divergente" not in ex.mensagem  # amostra: sem conferência exata
    q = db.execute(text("SELECT quarentena, quarentena_campos FROM bronze.registros WHERE sistema_origem='ISS' "
                        "AND quarentena IS NOT NULL AND dados->>'Nome' LIKE 'CC9 %'")).all()
    assert len(q) == 2 and all(m == "chave_nula" and c == ["Documento_SoNumeros"] for m, c in q)
    # prata ignora: nenhuma pendência/registro de silver com os nomes da quarentena
    from app.models.camadas import MunicipePendencia
    assert not db.query(MunicipePendencia).filter(MunicipePendencia.nome.in_(["CC9 NULA", "CC9 VAZIA"])).count()


def test_cc9_divergente_quando_nao_fecha(client, db):
    from app.services.carga_municipe import reconciliacao
    a = _rec(db)
    r = reconciliacao(db, a["bronze"] + a["quarentena"] + 1)
    assert not r["ok"] and r["status"] == "divergente"
    assert reconciliacao(db, a["bronze"] + a["quarentena"])["status"] == ("ok_com_pendencias" if a["quarentena"] else "ok")


def test_cc9_acima_do_limite_para(client, db, monkeypatch):
    from app.services.carga_municipe import executar_carga_municipe
    monkeypatch.delenv("SIGPMF_CARGA_NULOS_LIMITE_PCT", raising=False)
    monkeypatch.setenv("SIGPMF_CARGA_NULOS_LIMITE_MIN", "0")  # sem mínimo absoluto: 2 de 100 (> 1%) para
    nulas = [{**linha(None, nome=f"LIM {i}"), "Documento_SoNumeros": None} for i in range(2)]
    ex = executar_carga_municipe(db, "operador@aion.local", linhas=[_sonum(cpf(720000001))], nulos=nulas,
                                 total_origem=100, total_nulos=2, bronze=True)
    assert ex.status == "falha"
    monkeypatch.setenv("SIGPMF_CARGA_NULOS_LIMITE_PCT", "5")
    ex = executar_carga_municipe(db, "operador@aion.local", linhas=[_sonum(cpf(720000002))], nulos=nulas,
                                 total_origem=100, total_nulos=2)
    assert ex.status != "falha", ex.mensagem


def _carga_pequena(db, n_nulos, total=50, base_cpf=730000000):
    from app.services.carga_municipe import executar_carga_municipe
    boas = [_sonum(cpf(base_cpf + i)) for i in range(total - n_nulos)]
    nulas = [{**linha(None, nome=f"ZZTESTE MUN NULA {base_cpf}-{i}"), "Documento_SoNumeros": None} for i in range(n_nulos)]
    return executar_carga_municipe(db, "operador@aion.local", linhas=boas, nulos=nulas,
                                   total_origem=total, total_nulos=n_nulos, bronze=True)


def test_cc9_minimo_tabela_pequena_segue_com_quarentena(client, db, monkeypatch):
    monkeypatch.delenv("SIGPMF_CARGA_NULOS_LIMITE_PCT", raising=False)
    monkeypatch.delenv("SIGPMF_CARGA_NULOS_LIMITE_MIN", raising=False)
    ex = _carga_pequena(db, 1, base_cpf=730000000)  # 1 de 50 = 2% (> 1%), mas 1 <= 10
    assert ex.status != "falha", ex.mensagem
    assert db.query(RegistroBronze).filter(RegistroBronze.sistema_origem == "ISS",
                                           RegistroBronze.quarentena == "chave_nula",
                                           RegistroBronze.execucao_id == ex.id).count() == 1


def test_cc9_minimo_acima_dos_dois_limites_bloqueia(client, db, monkeypatch):
    monkeypatch.delenv("SIGPMF_CARGA_NULOS_LIMITE_PCT", raising=False)
    monkeypatch.delenv("SIGPMF_CARGA_NULOS_LIMITE_MIN", raising=False)
    ex = _carga_pequena(db, 11, base_cpf=731000000)  # 11 de 50 = 22%, > 1% e > 10
    assert ex.status == "falha"
    assert "22.00%" in ex.mensagem and "limite de 1%" in ex.mensagem and "mínimo de 10" in ex.mensagem


def test_cc9_minimo_customizado_por_env(client, db, monkeypatch):
    from app.services.carga_municipe import bloqueia_nulos
    monkeypatch.delenv("SIGPMF_CARGA_NULOS_LIMITE_PCT", raising=False)
    monkeypatch.setenv("SIGPMF_CARGA_NULOS_LIMITE_MIN", "0")
    ex = _carga_pequena(db, 1, base_cpf=732000000)
    assert ex.status == "falha" and "mínimo de 0" in ex.mensagem
    monkeypatch.setenv("SIGPMF_CARGA_NULOS_LIMITE_MIN", "25")
    assert not bloqueia_nulos(20, 50) and bloqueia_nulos(26, 50)
    assert not bloqueia_nulos(40, 5000)  # > mínimo mas <= 1%


def test_cc9_tabela_toda_nula_bloqueia_mesmo_abaixo_do_minimo(client, db, monkeypatch):
    from app.services.carga_municipe import bloqueia_nulos
    monkeypatch.delenv("SIGPMF_CARGA_NULOS_LIMITE_PCT", raising=False)
    monkeypatch.delenv("SIGPMF_CARGA_NULOS_LIMITE_MIN", raising=False)
    assert bloqueia_nulos(5, 5) and not bloqueia_nulos(4, 5) and not bloqueia_nulos(0, 0)
    ex = _carga_pequena(db, 5, total=5, base_cpf=733000000)  # ZZTESTE: 5 linhas, todas nulas (5 <= mínimo 10)
    assert ex.status == "falha"
    assert "todas as 5 linha(s)" in ex.mensagem and "100%" in ex.mensagem and "mínimo de 10" in ex.mensagem
    assert db.query(RegistroBronze).filter(RegistroBronze.execucao_id == ex.id).count() == 0


def test_cc9_selects_separados_limitados_e_pela_guarda():
    from app.services.carga_municipe import _sql_nulos
    q1, q2 = _sql_nulos(7)
    assert q1.startswith("SELECT COUNT(*)") and "IS NULL" in q1
    assert q2.startswith("SELECT TOP (7)") and "WHERE (" in q2
    for c in ISS.colunas["Municipes"]:
        assert f"[{c}]" in q2
    assert "Senha" not in q2


def test_cc9_ler_nulos_no_conector(monkeypatch):
    from app.services import carga_municipe as cm
    sqls = []

    class Conn:
        def close(self):
            pass

    def fake(conn, sql, params=None, limite=None, como_dict=True):
        sqls.append(sql)
        return [(1000, 3)] if sql.startswith("SELECT COUNT") else [{"Nome": "N"}] * 3

    monkeypatch.setattr("app.core.sql_guard.executa_somente_leitura", fake)
    c = type("C", (), {"_conectar": lambda self: Conn()})()
    assert cm.ler_nulos(c) == (1000, 3, [{"Nome": "N"}] * 3) and sqls[1].startswith("SELECT TOP (3)")
    sqls.clear()
    monkeypatch.setattr("app.core.sql_guard.executa_somente_leitura",
                        lambda *a, **k: [(100, 11)])  # 11% > 1% e 11 > mínimo 10: não lê as linhas
    assert cm.ler_nulos(c) == (100, 11, [])
    monkeypatch.setenv("SIGPMF_CARGA_NULOS_LIMITE_MIN", "0")
    monkeypatch.setattr("app.core.sql_guard.executa_somente_leitura", lambda *a, **k: [(100, 5)])
    assert cm.ler_nulos(c) == (100, 5, [])  # MIN=0: 5% > 1% já bloqueia


def test_cc9_amostra_nao_mostra_divergente(client, db):
    from app.services.carga_municipe import executar_carga_municipe, reconciliacao
    a = _rec(db)
    ex = executar_carga_municipe(db, "operador@aion.local", linhas=[_sonum(cpf(730000001), nome="AMOSTRA 1")],
                                 total_origem=366166, total_nulos=0, bronze=True)  # amostra << origem
    assert ex.status != "falha", ex.mensagem
    assert "status=amostra" in ex.mensagem and "divergente" not in ex.mensagem
    r = reconciliacao(db, 366166, amostra=True)
    assert r["status"] == "amostra" and r["ok"] is None and r["cor"] == "cinza"
    assert r["rotulo"] == "Amostra · reconciliação só na carga completa" and r["rotulo"] in ex.mensagem
    # carga completa mantém a igualdade exata
    assert reconciliacao(db, 366166)["status"] == "divergente"
    b = _rec(db)
    assert reconciliacao(db, b["bronze"] + b["quarentena"])["ok"] and b["bronze"] == a["bronze"] + 1
