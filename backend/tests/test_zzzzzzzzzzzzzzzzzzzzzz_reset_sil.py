"""Reset da SIL (scripts/reset_sil.py): só no Postgres de TESTE, com dados sintéticos ZZTESTE. Nenhuma chamada à API."""
import json
import uuid

import pytest
from sqlalchemy import create_engine, text

from scripts import reset_sil as rs
from tests.conftest import TEST_URL

pytestmark = pytest.mark.usefixtures("banco")
OUTRA = "SAUDE"


@pytest.fixture
def eng():
    assert "_test" in TEST_URL.rsplit("/", 1)[-1], "reset da SIL só roda em banco *_test"
    e = create_engine(TEST_URL)
    yield e
    with e.begin() as c:
        for s in [r[0] for r in c.execute(text("SELECT nspname FROM pg_namespace WHERE nspname LIKE 'zzteste_bkp%'"))]:
            c.execute(text(f'DROP SCHEMA "{s}" CASCADE'))
    e.dispose()


def _schema():
    return "zzteste_bkp_" + uuid.uuid4().hex[:8]


def _ex(c, sistema, status="sucesso"):
    return c.execute(text("INSERT INTO controle.execucoes (tipo, sistema, status, usuario) VALUES "
                          "('carga_completa', :s, :st, 'zzteste') RETURNING id"), {"s": sistema, "st": status}).scalar()


def _bronze(c, sistema, ex, amb="producao", quarentena=None):
    io = "ZZTESTE-" + uuid.uuid4().hex
    c.execute(text("INSERT INTO bronze.registros (sistema_origem, tabela, id_origem, dados, hash, execucao_id, "
                   "ambiente_origem, quarentena) VALUES (:s, 'Empresas', :io, CAST(:d AS JSONB), :h, :e, :a, :q)"),
              {"s": sistema, "io": io, "d": json.dumps({"Nome": "ZZTESTE"}), "h": uuid.uuid4().hex, "e": ex,
               "a": amb, "q": quarentena})


def _gold(c, cid, sistemas, dados):
    c.execute(text("INSERT INTO gold.cidadao_sistemas (cidadao_id, nome, sistemas, dominios) "
                   "VALUES (:c, 'ZZTESTE', CAST(:s AS JSONB), '[]'::jsonb)"), {"c": cid, "s": json.dumps(sistemas)})
    for sis in dados:
        c.execute(text("INSERT INTO gold.cidadao_dados (cidadao_id, dominio, campo, valor, sistema, mais_recente) "
                       "VALUES (:c, 'outros', 'cadastro', 'ZZTESTE', :s, true)"), {"c": cid, "s": sis})


@pytest.fixture
def cenario(eng):
    """SIL + outra fonte. Devolve os cidadãos do ouro por papel."""
    cids = {k: "zz" + uuid.uuid4().hex[:30] for k in ("compartilhado", "so_sil", "so_outra", "sil_com_obito")}
    with eng.begin() as c:
        c.execute(text("UPDATE controle.agendamentos SET ativo = false WHERE conector = 'sil'"))
        c.execute(text("UPDATE controle.execucoes SET status = 'falha' WHERE lower(sistema) = 'sil' AND status = 'em_andamento'"))
        s1, s2, o1 = _ex(c, "SIL"), _ex(c, "SIL"), _ex(c, OUTRA)
        for ex in (s1, s2, o1):
            c.execute(text("INSERT INTO controle.logs (execucao_id, nivel, mensagem) VALUES (:e, 'INFO', 'ZZTESTE')"), {"e": ex})
        _bronze(c, "SIL", s1)
        _bronze(c, "SIL", s1, amb="sandbox")
        _bronze(c, "SIL", s2, quarentena="chave_nula")
        _bronze(c, OUTRA, o1)
        _bronze(c, OUTRA, o1)
        for ccm in (990001, 990002):
            c.execute(text("INSERT INTO silver.empresas_sil (ccm, nome) VALUES (:c, 'ZZTESTE') ON CONFLICT DO NOTHING"), {"c": ccm})
        c.execute(text("DELETE FROM controle.checkpoints_carga WHERE lower(sistema) = 'sil'"))
        c.execute(text("INSERT INTO controle.checkpoints_carga (sistema, tabela, ambiente, posicao, ultima_chave, concluida, "
                       "execucao_id) VALUES ('SIL', 'Empresas', 'producao', 50, '[49]', false, :e), "
                       "('SIL', 'Empresas', 'sandbox', 49, '[48]', false, :e), "
                       "(:o, 'ZZTESTE_tab', 'producao', 7, '[7]', false, :x) ON CONFLICT DO NOTHING"),
                  {"e": s1, "o": OUTRA, "x": o1})
        c.execute(text("UPDATE controle.agendamentos SET ultima_execucao_id = :e, ultimo_status = 'sucesso', tentativas = 1 "
                       "WHERE conector = 'sil'"), {"e": s2})
        c.execute(text("INSERT INTO controle.camadas_estado (assinatura, execucao_id) VALUES ('{}'::jsonb, :e)"), {"e": s2})
        _gold(c, cids["compartilhado"], ["SAUDE", "SIL"], ["SIL", "SAUDE"])
        _gold(c, cids["so_sil"], ["SIL"], ["SIL"])
        _gold(c, cids["so_outra"], ["SAUDE"], ["SAUDE"])
        _gold(c, cids["sil_com_obito"], ["SIL"], ["SIL", "CEMITERIO"])
        c.execute(text("INSERT INTO controle.auditoria (usuario, acao, alvo, detalhe) VALUES ('zzteste', 'zzteste_previa', "
                       "'SIL', '{}'::jsonb)"))
    return cids


def _tudo(c):
    q = {
        "bronze_sil": "SELECT count(*) FROM bronze.registros WHERE upper(sistema_origem)='SIL'",
        "bronze_outra": "SELECT count(*) FROM bronze.registros WHERE upper(sistema_origem)<>'SIL'",
        "empresas": "SELECT count(*) FROM silver.empresas_sil",
        "ex_sil": "SELECT count(*) FROM controle.execucoes WHERE lower(sistema)='sil'",
        "ex_outra": "SELECT count(*) FROM controle.execucoes WHERE lower(coalesce(sistema,''))<>'sil'",
        "logs": "SELECT count(*) FROM controle.logs",
        "cp_sil": "SELECT count(*) FROM controle.checkpoints_carga WHERE lower(sistema)='sil'",
        "cp_outra": "SELECT count(*) FROM controle.checkpoints_carga WHERE lower(sistema)<>'sil'",
        "gold_dados": "SELECT count(*) FROM gold.cidadao_dados",
        "gold_cid": "SELECT count(*) FROM gold.cidadao_sistemas",
        "auditoria": "SELECT count(*) FROM controle.auditoria",
        "ce_null": "SELECT count(*) FROM controle.camadas_estado WHERE execucao_id IS NULL",
    }
    return {k: c.execute(text(v)).scalar() for k, v in q.items()}


def _cp_sil(c):
    return sorted(tuple(r) for r in c.execute(text(
        "SELECT ambiente, posicao, ultima_chave::text, execucao_id FROM controle.checkpoints_carga WHERE lower(sistema)='sil'")))


def _ag(c):
    return tuple(c.execute(text("SELECT ativo, ultima_execucao_id, ultimo_status, tentativas FROM controle.agendamentos "
                                "WHERE conector='sil'")).first())


def _schema_existe(c, s):
    return bool(c.execute(text("SELECT 1 FROM pg_namespace WHERE nspname=:s"), {"s": s}).scalar())


def test_simulacao_nao_muda_nada_nem_deixa_backup(eng, cenario):
    s = _schema()
    with eng.connect() as c:
        antes, cp, ag = _tudo(c), _cp_sil(c), _ag(c)
    out = []
    rel = rs.executar(eng, "ZZTESTE operador", confirmar=False, schema_backup=s, saida=out.append)
    assert rel.modo == "simulacao" and rel.antes["bronze.registros"] >= 3
    assert any("ROLLBACK" in x for x in out)
    with eng.connect() as c:
        assert _tudo(c) == antes and _cp_sil(c) == cp and _ag(c) == ag
        assert not _schema_existe(c, s)


def test_reset_so_sil_mantem_outras_fontes_e_auditoria(eng, cenario):
    s = _schema()
    with eng.connect() as c:
        antes = _tudo(c)
        aud_ids = [r[0] for r in c.execute(text("SELECT id FROM controle.auditoria"))]
    out = []
    rel = rs.executar(eng, "ZZTESTE operador", confirmar=True, schema_backup=s, saida=out.append)
    with eng.connect() as c:
        d = _tudo(c)
        assert d["bronze_sil"] == 0 and d["empresas"] == 0 and d["ex_sil"] == 0 and d["cp_sil"] == 0
        for k in ("bronze_outra", "ex_outra", "cp_outra"):
            assert d[k] == antes[k], k
        assert d["auditoria"] == antes["auditoria"] + 1
        assert set(aud_ids) <= {r[0] for r in c.execute(text("SELECT id FROM controle.auditoria"))}
        nova = c.execute(text("SELECT usuario, detalhe FROM controle.auditoria WHERE acao='reset_sil' ORDER BY id DESC LIMIT 1")).first()
        assert nova.usuario == "ZZTESTE operador" and nova.detalhe["antes"]["controle.execucoes"] == antes["ex_sil"]
        assert nova.detalhe["depois"]["bronze.registros"] == 0 and nova.detalhe["api_sil_chamada"] is False
        # agenda desligada e no estado inicial; checkpoint SIL some -> próxima carga recria posicao=0 (Sequencia=1)
        assert _ag(c) == (False, None, None, 0)
        assert _cp_sil(c) == []
        # ouro: padrão mantém o cidadão só SIL; só os dados SIL do compartilhado saem
        dados = dict(c.execute(text("SELECT cidadao_id, string_agg(sistema, ',' ORDER BY sistema) FROM gold.cidadao_dados "
                                    "WHERE cidadao_id = ANY(:ids) GROUP BY cidadao_id"), {"ids": list(cenario.values())}).all())
        assert dados[cenario["compartilhado"]] == "SAUDE"
        assert dados[cenario["so_sil"]] == "SIL"
        assert dados[cenario["so_outra"]] == "SAUDE"
        assert dados[cenario["sil_com_obito"]] == "CEMITERIO"  # o dado SIL sai, o cidadão e o óbito ficam
        # backup restrito ao dono e com as contagens do "antes"
        assert c.execute(text(f'SELECT count(*) FROM "{s}"."bronze__registros"')).scalar() == antes["bronze_sil"]
        assert not c.execute(text("SELECT has_schema_privilege('public', :s, 'USAGE')"), {"s": s}).scalar()
    assert rel.backup[f"{s}.controle__execucoes"] == antes["ex_sil"]
    assert not any("ZZTESTE-" in x for x in out)  # só contagens na saída


def test_flag_ouro_remove_so_quem_e_so_sil(eng, cenario):
    rs.executar(eng, "ZZTESTE operador", confirmar=True, remover_ouro_so_sil=True, schema_backup=_schema(), saida=lambda _: None)
    with eng.connect() as c:
        cs = {r[0] for r in c.execute(text("SELECT cidadao_id FROM gold.cidadao_sistemas WHERE cidadao_id = ANY(:i)"),
                                      {"i": list(cenario.values())})}
        assert cenario["so_sil"] not in cs
        assert {cenario["compartilhado"], cenario["so_outra"], cenario["sil_com_obito"]} <= cs
        n = c.execute(text("SELECT count(*) FROM gold.cidadao_dados WHERE cidadao_id=:c"), {"c": cenario["so_sil"]}).scalar()
        assert n == 0
        obito = c.execute(text("SELECT count(*) FROM gold.cidadao_dados WHERE cidadao_id=:c"),
                          {"c": cenario["sil_com_obito"]}).scalar()
        assert obito == 1  # proveniência compartilhada: cidadão fica, só o dado SIL sai


@pytest.mark.parametrize("ouro", [False, True])
def test_rollback_do_backup_restaura_contagens_e_checkpoint(eng, cenario, ouro):
    s = _schema()
    with eng.connect() as c:
        antes, cp, ag = _tudo(c), _cp_sil(c), _ag(c)
    rs.executar(eng, "ZZTESTE operador", confirmar=True, remover_ouro_so_sil=ouro, schema_backup=s, saida=lambda _: None)
    sql = rs.sql_rollback(s, ouro).replace(":'operador'", "'ZZTESTE operador'")
    stmts = [x for x in sql.splitlines() if x and not x.startswith("\\") and x not in ("BEGIN;", "COMMIT;")]
    with eng.begin() as c:
        for st in stmts:
            c.execute(text(st.rstrip(";")))
    with eng.connect() as c:
        depois = _tudo(c)
        assert depois["auditoria"] == antes["auditoria"] + 2  # reset + rollback, nada apagado
        assert {k: v for k, v in depois.items() if k not in ("auditoria", "ce_null")} == \
            {k: v for k, v in antes.items() if k not in ("auditoria", "ce_null")}
        assert depois["ce_null"] == antes["ce_null"]
        assert _cp_sil(c) == cp and _ag(c) == ag


def test_recusa_agenda_ativa(eng, cenario):
    with eng.begin() as c:
        c.execute(text("UPDATE controle.agendamentos SET ativo = true WHERE conector = 'sil'"))
        antes = _tudo(c)
    try:
        with pytest.raises(rs.Recusa, match="agenda"):
            rs.executar(eng, "ZZTESTE operador", confirmar=True, schema_backup=_schema(), saida=lambda _: None)
        with eng.connect() as c:
            assert _tudo(c) == antes
    finally:
        with eng.begin() as c:
            c.execute(text("UPDATE controle.agendamentos SET ativo = false WHERE conector = 'sil'"))


def test_recusa_execucao_em_andamento(eng, cenario):
    with eng.begin() as c:
        ex = _ex(c, "SIL", "em_andamento")
        antes = _tudo(c)
    try:
        with pytest.raises(rs.Recusa, match="em_andamento"):
            rs.executar(eng, "ZZTESTE operador", confirmar=True, schema_backup=_schema(), saida=lambda _: None)
        with eng.connect() as c:
            assert _tudo(c) == antes
    finally:
        with eng.begin() as c:
            c.execute(text("UPDATE controle.execucoes SET status='falha' WHERE id=:e"), {"e": ex})


def test_recusa_backup_existente_nao_vazio_e_operador_vazio(eng, cenario):
    s = _schema()
    with eng.begin() as c:
        c.execute(text(f'CREATE SCHEMA "{s}"'))
        c.execute(text(f'CREATE TABLE "{s}".x AS SELECT 1 AS n'))
        antes = _tudo(c)
    with pytest.raises(rs.Recusa, match="não está vazio"):
        rs.executar(eng, "ZZTESTE operador", confirmar=True, schema_backup=s, saida=lambda _: None)
    with pytest.raises(rs.Recusa, match="usuario"):
        rs.executar(eng, "   ", confirmar=True, schema_backup=_schema(), saida=lambda _: None)
    with pytest.raises(rs.Recusa):
        rs.executar(eng, "ZZTESTE operador", confirmar=True, schema_backup="gold", saida=lambda _: None)
    with eng.connect() as c:
        assert _tudo(c) == antes


def test_divergencia_no_backup_faz_rollback(eng, cenario, monkeypatch):
    s = _schema()
    with eng.connect() as c:
        antes = _tudo(c)
    real = rs._contagens

    def errada(c, lista):
        d = real(c, lista)
        return {k: v + 1 for k, v in d.items()}
    monkeypatch.setattr(rs, "_contagens", errada)
    with pytest.raises(rs.Divergencia, match="backup"):
        rs.executar(eng, "ZZTESTE operador", confirmar=True, schema_backup=s, saida=lambda _: None)
    with eng.connect() as c:
        assert _tudo(c) == antes and not _schema_existe(c, s)


def test_cli_codigos_de_saida(eng, cenario, monkeypatch, capsys):
    monkeypatch.setattr("app.core.db.get_engine", lambda: eng)
    assert rs.main(["--usuario", "ZZTESTE operador", "--schema-backup", _schema()]) == 0
    assert "SIMULAÇÃO" in capsys.readouterr().out
    assert rs.main(["--schema-backup", _schema()]) == 2
    assert rs.main(["--imprimir-rollback"]) == 0
