"""PR-03 modelo prata (S1–S4). Dados sintéticos ZZTESTE."""
import re
import uuid
from datetime import datetime, timezone

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text

from tests.conftest import TEST_URL, alembic_cfg

CPF_OK = "52998224725"  # sintético com DV válido


def _cidadao(db, nome="ZZTESTE PRATA"):
    cid = str(uuid.uuid4())
    db.execute(text("INSERT INTO silver.cidadaos (cidadao_id, nome) VALUES (:c, :n)"), {"c": cid, "n": nome})
    return cid


def _vinculo(db, cid, chave):
    db.execute(text("INSERT INTO silver.vinculos (cidadao_id, sistema, tabela, chave_origem, bronze_id, metodo, "
                    "confianca, atributos) VALUES (:c,'ZZTESTE','zz_tab',:k,0,'cpf',1.0,'[]')"), {"c": cid, "k": chave})


def test_s1_uma_ponta_e_colunas_aditivas(banco):
    assert ScriptDirectory.from_config(alembic_cfg()).get_heads() == ["0030"]
    insp = inspect(create_engine(TEST_URL))
    cols = {c["name"] for c in insp.get_columns("vinculos", schema="silver")}
    assert {"entidade", "tipo_chave", "chave_hash", "status", "ambiente_origem"} <= cols
    for t in ("empresa", "cidadao_empresa", "procedencia_valor"):
        assert insp.has_table(t, schema="silver")


def test_s2_contagem_vinculos_igual_antes_e_depois_e_sem_delete(db):
    cfg = alembic_cfg()
    for i in range(5):
        _vinculo(db, _cidadao(db), f"ZZTESTE-S2-{i}")
    db.commit()
    command.downgrade(cfg, "0026")
    eng = create_engine(TEST_URL)
    with eng.connect() as c:
        antes = c.execute(text("SELECT count(*) FROM silver.vinculos")).scalar()
        ids_antes = c.execute(text("SELECT array_agg(id ORDER BY id) FROM silver.vinculos")).scalar()
    command.upgrade(cfg, "head")
    command.upgrade(cfg, "head")  # idempotente
    with eng.connect() as c:
        assert c.execute(text("SELECT count(*) FROM silver.vinculos")).scalar() == antes
        assert c.execute(text("SELECT array_agg(id ORDER BY id) FROM silver.vinculos")).scalar() == ids_antes
        r = c.execute(text("SELECT DISTINCT entidade, status, ambiente_origem FROM silver.vinculos")).all()
        assert r == [("cidadao", "confirmado", "producao")]
    eng.dispose()
    with open(cfg.get_main_option("script_location") + "/versions/0030_modelo_prata.py") as f:
        up = f.read().split("def upgrade", 1)[1].split("def downgrade", 1)[0]
    assert not re.search(r"\bDELETE\s+FROM\b|\bTRUNCATE\b|\bUPDATE\s+silver\.", up, re.IGNORECASE)


def test_s3_procedencia_e_valor_mais_completo(db):
    from app.silver.modelo import registra_valor, valor_vencedor
    cid = _cidadao(db)
    d1, d2 = datetime(2024, 1, 1, tzinfo=timezone.utc), datetime(2025, 1, 1, tzinfo=timezone.utc)
    registra_valor(db, entidade="cidadao", entidade_id=cid, campo="nome", valor="ZZTESTE SILVA", sistema="SIL",
                   tabela="zz_a", chave_origem="1", data_referencia=d2)
    registra_valor(db, entidade="cidadao", entidade_id=cid, campo="nome", valor="ZZTESTE DA SILVA SOUZA",
                   sistema="IPTU", tabela="zz_b", chave_origem="2", data_referencia=d1)
    v = valor_vencedor(db, "cidadao", cid, "nome")
    assert v.valor == "ZZTESTE DA SILVA SOUZA" and v.sistema == "IPTU" and v.tabela == "zz_b"
    assert v.data_referencia == d1 and len(v.chave_hash) == 64 and v.chave_hash != "2"
    # empate de completude → o mais recente
    registra_valor(db, entidade="cidadao", entidade_id=cid, campo="mae", valor="ZZTESTE MAE A", sistema="SIL",
                   tabela="zz_a", chave_origem="1", data_referencia=d1)
    registra_valor(db, entidade="cidadao", entidade_id=cid, campo="mae", valor="ZZTESTE MAE B", sistema="ISS",
                   tabela="zz_c", chave_origem="3", data_referencia=d2)
    assert valor_vencedor(db, "cidadao", cid, "mae").sistema == "ISS"
    # vazio nunca vence e nenhum candidato some
    registra_valor(db, entidade="cidadao", entidade_id=cid, campo="mae", valor="  ", sistema="X", tabela="zz_d",
                   chave_origem="4", data_referencia=d2)
    n = db.execute(text("SELECT count(*) FROM silver.procedencia_valor WHERE entidade_id=:c AND campo='mae'"),
                   {"c": cid}).scalar()
    assert n == 3 and valor_vencedor(db, "cidadao", cid, "mae").sistema == "ISS"
    db.rollback()


def test_s4_ligacao_empresa_exige_cpf_valido(db, monkeypatch):
    from app.models.prata_modelo import Empresa
    from app.silver.modelo import CpfInvalidoParaLigacao, liga_cidadao_empresa
    cid = _cidadao(db)
    e = Empresa(inscricao_municipal="ZZ1", fonte_inscricao="ZZTESTE", razao_social="ZZTESTE ME")
    db.add(e)
    db.flush()
    for ruim in ("11111111111", "12345678900", "1234567890", None, "12345678909"):
        with pytest.raises(CpfInvalidoParaLigacao):
            liga_cidadao_empresa(db, cidadao_id=cid, empresa_id=e.id, cpf=ruim, papel="mei", sistema="ZZ", tabela="t")
    lig = liga_cidadao_empresa(db, cidadao_id=cid, empresa_id=e.id, cpf=CPF_OK, papel="mei", sistema="ZZ", tabela="t")
    assert lig.id and lig.chave_hash != CPF_OK
    from app.core.config import get_settings
    monkeypatch.setenv("CPF_PREENCHIMENTO", CPF_OK)  # N3a: lista vem de configuração (via cpf.classifica)
    get_settings.cache_clear()
    try:
        with pytest.raises(CpfInvalidoParaLigacao):
            liga_cidadao_empresa(db, cidadao_id=cid, empresa_id=e.id, cpf=CPF_OK, papel="socio", sistema="ZZ",
                                 tabela="t")
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()
    db.rollback()


def test_empresa_exige_cnpj_ou_inscricao(db):
    from sqlalchemy.exc import IntegrityError
    with pytest.raises(IntegrityError):
        db.execute(text("INSERT INTO silver.empresa (razao_social) VALUES ('ZZTESTE SEM CHAVE')"))
    db.rollback()


def test_vinculo_probabilistico_nunca_confirmado():
    from app.silver.modelo import status_vinculo
    assert status_vinculo("cpf", 0.5) == ("confirmado", 1.0)
    st, conf = status_vinculo("nome_nascimento", 1.0)
    assert st == "revisao" and conf < 1.0


def test_status_fora_do_dominio_recusado(db):
    from sqlalchemy.exc import IntegrityError
    cid = _cidadao(db)
    with pytest.raises(IntegrityError):
        db.execute(text("INSERT INTO silver.vinculos (cidadao_id, sistema, tabela, chave_origem, bronze_id, metodo, "
                        "confianca, atributos, status) VALUES (:c,'ZZ','t','k',0,'cpf',1,'[]','talvez')"), {"c": cid})
    db.rollback()


# --- R1 do QA #48: HMAC-SHA256 com segredo do ambiente (dados sintéticos ZZTESTE) ---

def _com_segredo(monkeypatch, valor):
    from app.core.config import get_settings
    if valor is None:
        monkeypatch.delenv("SIGPMF_CHAVE_HASH_SEGREDO", raising=False)
    else:
        monkeypatch.setenv("SIGPMF_CHAVE_HASH_SEGREDO", valor)
    get_settings.cache_clear()


def test_r1_chave_hash_e_hmac_com_segredo(monkeypatch):
    import hashlib
    import hmac

    from app.core.config import get_settings
    from app.silver.modelo import chave_hash
    try:
        _com_segredo(monkeypatch, "ZZTESTE-segredo-A-" + "a" * 32)
        h_a = chave_hash(" ZZTESTE-INSC-1 ")
        assert h_a == hmac.new(("ZZTESTE-segredo-A-" + "a" * 32).encode(), b"ZZTESTE-INSC-1",
                               hashlib.sha256).hexdigest()
        assert h_a != hashlib.sha256(b"ZZTESTE-INSC-1").hexdigest()  # não é mais sha256 sem sal
        assert chave_hash("ZZTESTE-INSC-1") == h_a and len(h_a) == 64
        _com_segredo(monkeypatch, "ZZTESTE-segredo-B-" + "b" * 32)
        assert chave_hash("ZZTESTE-INSC-1") != h_a  # depende do segredo
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()


def test_r1_sem_segredo_recusa_calcular_e_subir(monkeypatch):
    from app.core.config import get_settings
    from app.main import create_app
    from app.silver.modelo import ChaveHashSemSegredo, chave_hash
    try:
        _com_segredo(monkeypatch, None)
        with pytest.raises(ChaveHashSemSegredo):
            chave_hash("ZZTESTE-INSC-1")
        create_app()  # em teste sobe, mas não calcula
        monkeypatch.setenv("APP_ENV", "prod")
        get_settings.cache_clear()
        with pytest.raises(RuntimeError, match="SIGPMF_CHAVE_HASH_SEGREDO"):
            create_app()
        _com_segredo(monkeypatch, "ZZTESTE-curto")  # menos de 32 caracteres: não sobe e não ecoa o valor
        with pytest.raises(RuntimeError) as exc:
            create_app()
        assert "ZZTESTE-curto" not in str(exc.value)
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()


def test_r1_segredo_nunca_aparece_em_repr_nem_log(monkeypatch, caplog):
    from app.core.config import get_settings
    segredo = "ZZTESTE-segredo-que-nao-pode-vazar-" + "x" * 8
    try:
        _com_segredo(monkeypatch, segredo)
        s = get_settings()
        assert segredo not in repr(s) and segredo not in str(s)
        from app.silver.modelo import chave_hash
        chave_hash("ZZTESTE-INSC-9")
        assert segredo not in caplog.text
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()


@pytest.mark.parametrize("vazio", [" " * 40, "\t" * 32, " \t" * 20])
def test_r1_segredo_so_espacos_e_recusado(monkeypatch, vazio):
    from app.core.config import get_settings
    from app.main import create_app
    from app.silver.modelo import ChaveHashSemSegredo, chave_hash
    try:
        _com_segredo(monkeypatch, vazio)
        with pytest.raises(ChaveHashSemSegredo):
            chave_hash("ZZTESTE-INSC-1")
        monkeypatch.setenv("APP_ENV", "prod")
        get_settings.cache_clear()
        with pytest.raises(RuntimeError, match="SIGPMF_CHAVE_HASH_SEGREDO"):
            create_app()
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()


def test_r1_segredo_secretstr_nao_vaza_em_dump(monkeypatch):
    from pydantic import SecretStr

    from app.core.config import get_settings
    segredo = "ZZTESTE-segredo-dump-" + "y" * 20
    try:
        _com_segredo(monkeypatch, segredo)
        s = get_settings()
        assert isinstance(s.chave_hash_segredo, SecretStr)
        assert segredo not in str(s.model_dump()) and segredo not in s.model_dump_json()
        assert segredo not in str(s.model_dump(by_alias=True)) and segredo not in s.model_dump_json(by_alias=True)
        assert s.segredo_chave_hash_ok() == segredo
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()
