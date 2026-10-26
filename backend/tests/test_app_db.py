from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text

from tests.conftest import TEST_URL, alembic_cfg


def test_app_inicia(client):
    from app.main import app
    assert app.title == "SIGPMF"


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["db"] == "ok" and r.json()["versao"]
    assert client.get("/api/health").status_code == 200


def test_docs_desligada_fora_de_dev(client):
    assert client.get("/api/docs").status_code == 404
    assert client.get("/api/openapi.json").status_code == 404


def test_conexao_db(banco):
    eng = create_engine(TEST_URL)
    with eng.connect() as c:
        assert c.execute(text("select 1")).scalar() == 1


def test_quatro_schemas(banco):
    eng = create_engine(TEST_URL)
    with eng.connect() as c:
        r = {x for (x,) in c.execute(text(
            "select schema_name from information_schema.schemata where schema_name in ('bronze','silver','gold','controle')"))}
    assert r == {"bronze", "silver", "gold", "controle"}


def test_bronze_colunas(banco):
    eng = create_engine(TEST_URL)
    with eng.connect() as c:
        cols = dict(c.execute(text("select column_name, data_type from information_schema.columns "
                                   "where table_schema='bronze' and table_name='registros'")).all())
    for k in ("sistema_origem", "id_origem", "extraido_em", "hash", "execucao_id"):
        assert k in cols
    assert cols["dados"] == "jsonb"


def _schema(eng):
    with eng.connect() as c:
        return c.execute(text("select table_schema, table_name, column_name, data_type from information_schema.columns "
                              "where table_schema in ('bronze','silver','gold','controle') order by 1,2,3")).all()


def test_migracao_idempotente_e_downgrade(banco):
    cfg = alembic_cfg()
    eng = create_engine(TEST_URL)
    antes = _schema(eng)
    command.upgrade(cfg, "head")
    assert _schema(eng) == antes
    command.downgrade(cfg, "-1")
    command.upgrade(cfg, "head")
    assert _schema(eng) == antes
    assert len(ScriptDirectory.from_config(cfg).get_heads()) == 1
