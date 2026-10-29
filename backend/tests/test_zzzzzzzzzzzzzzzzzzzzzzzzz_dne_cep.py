"""PR-02 DNE/CEP (E1-E4). Só dados sintéticos ZZTESTE; CEPs na faixa 999xxxxx (inexistente nos Correios)."""
import random

import pytest
from sqlalchemy import func, select

from app.models.camadas import RegistroBronze
from app.models.controle import Execucao
from app.services import dne

TABELA = "LOG_LOGRADOURO_SP"


@pytest.fixture
def ex(db):
    e = Execucao(tipo="carga_dne", sistema=dne.SISTEMA, status="em_andamento", usuario="zz@aion.local")
    db.add(e)
    db.commit()
    return e.id


def _ceps(n):
    base = random.randint(0, 9000)  # a referência sobrevive entre sessões: cada teste usa CEPs novos
    return [f"999{base + i:05d}" for i in range(n)]


def _linha(nu, cep, nome, bairro="ZZTESTE Centro"):
    return {"LOG_NU": nu, "CEP": cep, "LOG_NO": nome, "TLO_TX": "Rua", "BAI_NO": bairro, "LOC_NO": "ZZTESTE Cidade", "UFE_SG": "SP"}


def _n_ref(db, ceps):
    return db.scalar(select(func.count()).select_from(dne.dne_logradouro).where(dne.dne_logradouro.c.cep.in_(ceps)))


def test_e1_contagem_origem_igual_bronze(db, ex):
    ceps = _ceps(5)
    tabela = "DNE_GU_SP_LOGRADOUROS"
    linhas = [_linha(f"ZZ{c}", c, f"ZZTESTE Logradouro {i}") for i, c in enumerate(ceps)]
    linhas.append({"LOG_NU": f"ZZ{ceps[0]}x", "CEP": "1", "LOG_NO": "ZZTESTE sem cep"})  # fica só na bronze
    antes = db.scalar(select(func.count(func.distinct(RegistroBronze.id_origem)))
                      .where(RegistroBronze.sistema_origem == dne.SISTEMA, RegistroBronze.tabela == tabela)) or 0
    r = dne.importa(db, tabela, linhas, ex)
    assert r["origem"] == 6 and r["bronze_novos"] == 6 and r["novos"] == 5 and r["sem_cep"] == 1
    rec = dne.reconcilia(db, tabela, antes + len(linhas))
    assert rec["confere"] and rec["bronze"] == antes + 6
    assert not dne.reconcilia(db, tabela, antes + len(linhas) + 1)["confere"]
    assert _n_ref(db, ceps) == 5


def test_e1_tabela_fora_da_allowlist(db, ex):
    with pytest.raises(ValueError) as e:
        dne.importa(db, "LOG_LOGRADOURO_SP_BKP", [_linha("1", "99900000", "ZZ")], ex)
    assert str(e.value) == "tabela_nao_permitida"


def test_e2_logradouro_vem_do_dne_e_codigo_logradouro_nao_e_chave(db, ex):
    a, b = _ceps(2)
    dne.importa(db, TABELA, [_linha(f"ZZ{a}", a, "ZZTESTE das Flores"), _linha(f"ZZ{b}", b, "ZZTESTE dos Ipes")], ex)
    # mesmo Codigo_Logradouro em dois registros com CEPs diferentes: cada um resolve pelo seu CEP
    r1 = dne.resolve_registro(db, {"CEP": f"{a[:5]}-{a[5:]}", "Logradouro": "R FLORES", "Codigo_Logradouro": 42})
    r2 = dne.resolve_registro(db, {"CEP": b, "Logradouro": "qualquer", "Codigo_Logradouro": 42})
    assert r1["logradouro"] == "Rua ZZTESTE das Flores" and r1["valido_dne"] and r1["motivo"] is None and r1["cep"] == a
    assert r2["logradouro"] == "Rua ZZTESTE dos Ipes" and r2["uf"] == "SP" and r2["localidade"] == "ZZTESTE Cidade"
    # Codigo_Logradouro sozinho (sem CEP) nunca resolve
    r3 = dne.resolve_registro(db, {"Codigo_Logradouro": 42, "Logradouro": "ZZTESTE cru"})
    assert r3["valido_dne"] is False and r3["motivo"] == "cep_nao_encontrado"
    assert "Codigo_Logradouro" not in dne.resolve.__code__.co_varnames


@pytest.mark.parametrize("cep", [None, "", "1", "00000000", "999999999", "ZZTESTE"])
def test_e3_cep_ausente_ou_lixo_mantem_origem(db, cep):
    r = dne.resolve(db, cep, "  ZZTESTE   Rua Crua ", "ZZTESTE Bairro")
    assert r == {"cep": None, "logradouro": "ZZTESTE Rua Crua", "bairro": "ZZTESTE Bairro", "localidade": None, "uf": None,
                 "valido_dne": False, "motivo": "cep_nao_encontrado"}


def test_e3_cep_valido_fora_do_dne_mantem_origem(db):
    r = dne.resolve(db, "99999998", "ZZTESTE Rua Crua")
    assert r["cep"] == "99999998" and r["logradouro"] == "ZZTESTE Rua Crua" and r["motivo"] == "cep_nao_encontrado"


def test_e4_dne_novo_atualiza_por_upsert_sem_duplicar(db, ex):
    a, b, c = _ceps(3)
    r = dne.importa(db, TABELA, [_linha(f"ZZ{a}", a, "ZZTESTE Velha"), _linha(f"ZZ{b}", b, "ZZTESTE Igual")], ex)
    assert (r["novos"], r["atualizados"], r["iguais"]) == (2, 0, 0)
    r = dne.importa(db, TABELA, [_linha(f"ZZ{a}", a, "ZZTESTE Nova"), _linha(f"ZZ{b}", b, "ZZTESTE Igual"),
                                 _linha(f"ZZ{c}", c, "ZZTESTE Terceira"), _linha(f"ZZ{c}b", c, "ZZTESTE Terceira B")], ex)
    assert (r["novos"], r["atualizados"], r["iguais"]) == (1, 1, 1)
    assert _n_ref(db, [a, b, c]) == 3
    assert dne.resolve(db, a)["logradouro"] == "Rua ZZTESTE Nova"
    assert dne.resolve(db, c)["logradouro"] == "Rua ZZTESTE Terceira B"  # o último do arquivo vence
    r = dne.importa(db, TABELA, [_linha(f"ZZ{a}", a, "ZZTESTE Nova")], ex)  # reimportar igual não muda nada
    assert (r["novos"], r["atualizados"], r["iguais"], r["bronze_novos"]) == (0, 0, 1, 0)


def test_cep8_e_linha_ref():
    assert dne.cep8(1440000) == "01440000" and dne.cep8("14400-000") == "14400000"
    assert dne.cep8(True) is None and dne.cep8("123") is None
    assert dne.linha_ref({"CEP": "99900001"}) is None  # sem logradouro
    r = dne.linha_ref({"CEP": "99900001", "LOG_NO": "ZZTESTE"})
    assert r["dne_id"] is None and len(r["hash"]) == 64


def test_migracao_tem_uma_ponta_so():
    from alembic.script import ScriptDirectory

    from tests.conftest import alembic_cfg
    heads = ScriptDirectory.from_config(alembic_cfg()).get_heads()
    assert len(heads) == 1
