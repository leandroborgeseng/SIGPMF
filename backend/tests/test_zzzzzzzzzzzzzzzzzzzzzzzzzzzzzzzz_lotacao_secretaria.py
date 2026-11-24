"""De-para de lotação por segmento (FL5/FL8/FL9). Dados ZZTESTE sintéticos, banco _test, sem pessoa."""
from sqlalchemy import text

from app.paineis import lotacao
from app.paineis.lotacao import OUTROS_VINCULOS, SECRETARIA, SEM_LOTACAO, Resolvedor, agrupa_drill
from app.paineis.privacidade import OUTROS, k_do_cubo

R = Resolvedor()
K = k_do_cubo("rh")

# fixture sintética: (codloc, n, folha, he) — ZZTESTE
ZZTESTE = [
    {"codloc": "1.1", "n": 6, "m1": 30000, "m2": 600, "zz": "ZZTESTE gabinete"},
    {"codloc": "1.1.5", "n": 4, "m1": 20000, "m2": 400, "zz": "ZZTESTE gabinete órgão"},
    {"codloc": "1.10.3", "n": 40, "m1": 200000, "m2": 9000, "zz": "ZZTESTE saúde UBS"},
    {"codloc": "1.10.3.2", "n": 10, "m1": 50000, "m2": 1000, "zz": "ZZTESTE saúde sub"},
    {"codloc": "1.6.1", "n": 30, "m1": 120000, "m2": 2000, "zz": "ZZTESTE educação"},
    {"codloc": "1.16.2", "n": 3, "m1": 9000, "m2": 300, "zz": "ZZTESTE esporte (pequeno)"},
    {"codloc": "1.9.1", "n": 2, "m1": 6000, "m2": 200, "zz": "ZZTESTE meio ambiente (pequeno)"},
    {"codloc": "1.13.1", "n": 8, "m1": 16000, "m2": 800, "zz": "ZZTESTE aposentados"},
    {"codloc": "1.12", "n": 2, "m1": 4000, "m2": 100, "zz": "ZZTESTE cedidos"},
    {"codloc": "1", "n": 7, "m1": 14000, "m2": 70, "zz": "ZZTESTE raiz"},
    {"codloc": "9.9", "n": 1, "m1": 1000, "m2": 10, "zz": "ZZTESTE desconhecido"},
]


def test_saude_nao_cai_no_gabinete():
    lot = R.resolve("1.10.3")
    assert (lot.secretaria, lot.orgao, lot.nome, lot.grupo) == ("1.10", "1.10.3", "Saúde", SECRETARIA)
    assert not lotacao.descendente("1.10.3", "1.1") and not lotacao.descendente("1.10", "1.1")
    assert lotacao.descendente("1.10.3", "1.10")


def test_gabinete():
    lot = R.resolve("1.1.5")
    assert (lot.secretaria, lot.orgao, lot.nome) == ("1.1", "1.1.5", "Gabinete do Prefeito")
    assert R.resolve("1.1").orgao is None and R.resolve("1.1.5.9").orgao == "1.1.5"


def test_outros_vinculos_e_sem_lotacao():
    for c in ("1.12", "1.13.4", "1.14.1.2", "1.15"):
        lot = R.resolve(c)
        assert lot.grupo == OUTROS_VINCULOS and lot.nome == "Outros vínculos" and not lot.no_ranking
    for c in ("1", "", None, "9.9", "1.99", "1..3", "10.1"):
        lot = R.resolve(c)
        assert lot.grupo == SEM_LOTACAO and lot.nome == "Sem lotação" and lot.secretaria is None


def test_sql_helper_por_segmento():
    s = lotacao.sql_descendente("t.codloc")
    assert s == "(t.codloc = :sec OR t.codloc LIKE :sec || '.%')"
    assert "LEFT(" not in s.upper()


def test_drill_totais_ranking_media_e_k():
    d = agrupa_drill(ZZTESTE, K)
    secs = [i.get("secretaria") for i in d["ranking"] if not i.get("outros")]
    assert secs == ["1.1", "1.6", "1.10"]  # ordem do de-para; Meio Ambiente/Esporte (n<5) suprimidos
    assert all(s not in ("1.12", "1.13", "1.14", "1.15") for s in secs)
    outros = [i for i in d["ranking"] if i.get("outros")]
    assert outros and outros[0]["rotulo"] == OUTROS and outros[0]["n"] >= K
    gab = next(i for i in d["ranking"] if i.get("secretaria") == "1.1")
    sau = next(i for i in d["ranking"] if i.get("secretaria") == "1.10")
    assert gab["n"] == 10 and sau["n"] == 50  # 1.10.* não entra no Gabinete
    assert d["outros_vinculos"]["n"] == 10 and d["outros_vinculos"]["m2"] == 900
    assert d["sem_lotacao"]["n"] == 8  # raiz + desconhecido
    assert d["total"]["n"] == sum(x["n"] for x in ZZTESTE)
    assert d["total"]["m2"] == sum(x["m2"] for x in ZZTESTE)
    assert sum(i["n"] for i in d["ranking"]) + d["outros_vinculos"]["n"] + d["sem_lotacao"]["n"] == d["total"]["n"]
    assert d["prefeitura"]["n"] == 6 + 4 + 40 + 10 + 30 + 2 + 3
    assert d["prefeitura"]["media_m2"] == (600 + 400 + 9000 + 1000 + 2000 + 200 + 300) / 95


def test_k_por_grupo_especial():
    d = agrupa_drill([{"codloc": "1.13", "n": 2, "m1": 10, "m2": 5}, {"codloc": "1", "n": 3, "m1": 10, "m2": 5},
                      {"codloc": "1.6", "n": 20, "m1": 100, "m2": 50}], K)
    assert d["outros_vinculos"]["protegido"] and d["outros_vinculos"]["m2"] is None
    assert d["sem_lotacao"]["protegido"] and d["sem_lotacao"]["m1"] is None
    assert d["total"]["n"] == 25


def test_tabela_de_para_migrada(db):
    assert db.scalar(text("SELECT current_database()")).find("_test") >= 0
    linhas = lotacao.carrega_de_para(db)
    assert set(linhas) == set(lotacao.DE_PARA_PADRAO)
    grupos = {c: g for c, _, g, _ in linhas}
    assert [c for c, g in grupos.items() if g == SECRETARIA] and len([g for g in grupos.values() if g == SECRETARIA]) == 12
    assert grupos["1"] == SEM_LOTACAO and grupos["1.10"] == SECRETARIA
    assert Resolvedor(linhas).resolve("1.10.3").nome == "Saúde"
