"""PR-02 DNE/CEP (E1-E4), fonte LOG_LOGRADOURO_SP + LOG_BAIRRO + LOG_LOCALIDADE.

Só dados sintéticos ZZTESTE; CEPs na faixa 999xxxxx (inexistente nos Correios).
"""
import logging
import random

import pytest
from sqlalchemy import func, select

from app.core.sql_guard import garante_somente_leitura
from app.models.camadas import RegistroBronze
from app.models.controle import Execucao
from app.services import dne

FRANCA, OUTRA = 3516200, 3550308


@pytest.fixture
def ex(db):
    e = Execucao(tipo="carga_dne", sistema=dne.SISTEMA, status="em_andamento", usuario="zz@aion.local")
    db.add(e)
    db.commit()
    return e.id


_seq = [random.randint(1, 9000)]


def _ceps(n):
    _seq[0] += n + 1  # a referência sobrevive entre sessões: cada teste usa CEPs/ids novos
    return [f"999{(_seq[0] + i) % 100000:05d}" for i in range(n)]


def _id(cep, k=0):
    return int(cep) * 10 + k


def _linha(cep, nome, ibge=FRANCA, k=0, usa_tipo="S", bairro="ZZTESTE Centro"):
    return {"Id_Logradouro": _id(cep, k), "Nome_Logradouro": nome, "Nome_Abreviado_Logradouro": nome[:10],
            "Tipo_Logradouro": "Rua", "Utiliza_Tipo_Logradouro": usa_tipo, "UF": "SP", "Cep": cep, "Complemento": None,
            "Nome_Bairro": bairro, "Nome_Localidade": "ZZTESTE Cidade", "Codigo_IBGE": ibge}


def _n_ref(db, ceps):
    return db.scalar(select(func.count()).select_from(dne.dne_logradouro).where(dne.dne_logradouro.c.cep.in_(ceps)))


def _n_bronze(db):
    return db.scalar(select(func.count(func.distinct(RegistroBronze.id_origem)))
                     .where(RegistroBronze.sistema_origem == dne.SISTEMA, RegistroBronze.tabela == dne.TABELA)) or 0


def test_sql_da_origem_e_somente_select_nas_tabelas_do_dba():
    for sql in (dne.SQL_LOGRADOUROS, dne.SQL_CONTAGENS):
        assert garante_somente_leitura(sql) == sql
        assert "[dbo].[LOG_LOGRADOURO_SP]" in sql and "[dbo].[LOG_BAIRRO]" in sql and "[dbo].[LOG_LOCALIDADE]" in sql
        assert "[Id_Bairro_Inicial]" in sql and "WITH (NOLOCK)" in sql
    assert "COUNT(*)" in dne.SQL_CONTAGENS and "[Codigo_IBGE] = %s" in dne.SQL_CONTAGENS
    for antigo in ("LOG_NO", "TLO_TX", "BAI_NO", "LOC_NO", "UFE_SG", "LOG_NU", "DNE_GU_SP"):
        assert antigo not in dne.SQL_LOGRADOUROS


def test_e1_contagem_origem_igual_bronze_e_log_so_de_agregados(db, ex, caplog):
    caplog.set_level(logging.INFO, logger="app.services.dne")
    ceps = _ceps(5)
    linhas = [_linha(c, f"ZZTESTE Logradouro {i}", FRANCA if i < 3 else OUTRA) for i, c in enumerate(ceps)]
    linhas.append(dict(_linha(ceps[0], "ZZTESTE sem cep", k=9), Cep="1"))  # fica só na bronze
    antes = _n_bronze(db)
    r = dne.importa(db, linhas, ex)
    assert (r["origem"], r["bronze_novos"], r["novos"], r["sem_cep"]) == (6, 6, 5, 1)
    assert (r["total_sp"], r["municipio_ibge"], r["total_municipio"]) == (6, FRANCA, 4)
    assert "dne_contagens total_sp=6 municipio_ibge=3516200 total_municipio=4" in caplog.text
    assert "ZZTESTE" not in caplog.text and ceps[0] not in caplog.text  # nenhum valor de linha no log
    rec = dne.reconcilia(db, antes + len(linhas))
    assert rec["confere"] and rec["bronze"] == antes + 6
    assert not dne.reconcilia(db, antes + len(linhas) + 1)["confere"]
    assert _n_ref(db, ceps) == 5


def test_e1_municipio_da_contagem_vem_da_configuracao(monkeypatch):
    linhas = [_linha("99900001", "ZZ", FRANCA), _linha("99900002", "ZZ", OUTRA)]
    assert dne.contagens(linhas)["total_municipio"] == 1
    monkeypatch.setenv("DNE_IBGE_CONTAGEM", str(OUTRA))
    assert dne.contagens(linhas) == {"total_sp": 2, "municipio_ibge": OUTRA, "total_municipio": 1}
    monkeypatch.setenv("DNE_IBGE_CONTAGEM", "lixo")
    assert dne.ibge_contagem() == FRANCA


def test_e1_tabela_fora_da_allowlist(db, ex):
    with pytest.raises(ValueError) as e:
        dne.importa(db, [_linha("99900000", "ZZ")], ex, tabela="DNE_GU_SP_LOGRADOUROS")
    assert str(e.value) == "tabela_nao_permitida"


def test_e2_mapeamento_e_codigo_logradouro_nao_e_chave(db, ex):
    a, b = _ceps(2)
    dne.importa(db, [_linha(a, "ZZTESTE das Flores", bairro="ZZTESTE Jardim"),
                     _linha(b, "ZZTESTE dos Ipes", usa_tipo="N")], ex)
    r1 = dne.resolve_registro(db, {"CEP": f"{a[:5]}-{a[5:]}", "Logradouro": "R FLORES", "Codigo_Logradouro": 42})
    r2 = dne.resolve_registro(db, {"CEP": b, "Logradouro": "qualquer", "Codigo_Logradouro": 42})
    assert r1 == {"cep": a, "logradouro": "Rua ZZTESTE das Flores", "bairro": "ZZTESTE Jardim", "localidade": "ZZTESTE Cidade",
                  "uf": "SP", "codigo_ibge": FRANCA, "valido_dne": True, "motivo": None}
    assert r2["logradouro"] == "ZZTESTE dos Ipes"  # Utiliza_Tipo_Logradouro = 'N': sem o tipo
    r3 = dne.resolve_registro(db, {"Codigo_Logradouro": 42, "Logradouro": "ZZTESTE cru"})
    assert r3["valido_dne"] is False and r3["motivo"] == "cep_nao_encontrado"
    assert "Codigo_Logradouro" not in dne.resolve.__code__.co_varnames


@pytest.mark.parametrize("cep", [None, "", "1", "00000000", "999999999", "ZZTESTE"])
def test_e3_cep_ausente_ou_lixo_mantem_origem(db, cep):
    r = dne.resolve(db, cep, "  ZZTESTE   Rua Crua ", "ZZTESTE Bairro")
    assert r == {"cep": None, "logradouro": "ZZTESTE Rua Crua", "bairro": "ZZTESTE Bairro", "localidade": None, "uf": None,
                 "codigo_ibge": None, "valido_dne": False, "motivo": "cep_nao_encontrado"}


def test_e3_cep_valido_fora_do_dne_mantem_origem(db):
    r = dne.resolve(db, "99999998", "ZZTESTE Rua Crua")
    assert r["cep"] == "99999998" and r["logradouro"] == "ZZTESTE Rua Crua" and r["motivo"] == "cep_nao_encontrado"


def test_e4_upsert_sem_duplicar_e_cep_repetido_deterministico(db, ex):
    a, b, c = _ceps(3)
    r = dne.importa(db, [_linha(a, "ZZTESTE Velha"), _linha(b, "ZZTESTE Igual")], ex)
    assert (r["novos"], r["atualizados"], r["iguais"], r["duplicados"]) == (2, 0, 0, 0)
    # c aparece 2x: vence o menor Id_Logradouro, em qualquer ordem
    r = dne.importa(db, [_linha(a, "ZZTESTE Nova"), _linha(b, "ZZTESTE Igual"),
                         _linha(c, "ZZTESTE Maior", k=5), _linha(c, "ZZTESTE Menor", k=1)], ex)
    assert (r["novos"], r["atualizados"], r["iguais"], r["duplicados"]) == (1, 1, 1, 1)
    assert _n_ref(db, [a, b, c]) == 3
    assert dne.resolve(db, a)["logradouro"] == "Rua ZZTESTE Nova"
    assert dne.resolve(db, c)["logradouro"] == "Rua ZZTESTE Menor"
    r = dne.importa(db, [_linha(c, "ZZTESTE Menor", k=1), _linha(c, "ZZTESTE Maior", k=5)], ex)  # ordem invertida
    assert (r["iguais"], r["atualizados"], r["duplicados"], r["bronze_novos"]) == (1, 0, 1, 0)


def test_linha_ref_e_cep8():
    assert dne.cep8(1440000) == "01440000" and dne.cep8("14400-000") == "14400000"
    assert dne.cep8(True) is None and dne.cep8("123") is None
    assert dne.linha_ref({"Cep": "99900001"}) is None  # sem logradouro
    r = dne.linha_ref({"Cep": "99900001", "Nome_Logradouro": "ZZTESTE", "Codigo_IBGE": "x"})
    assert r["id_logradouro"] is None and r["codigo_ibge"] is None and r["tipo_logradouro"] is None and len(r["hash"]) == 64


def test_migracao_tem_uma_ponta_so():
    from alembic.script import ScriptDirectory

    from tests.conftest import alembic_cfg
    assert len(ScriptDirectory.from_config(alembic_cfg()).get_heads()) == 1
