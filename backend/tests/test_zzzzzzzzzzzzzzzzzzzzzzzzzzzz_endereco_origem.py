"""EN1, EN4, EN7, EN10–EN12 e EN14: endereço da Saúde (Rua/Bairro do Geral, depois o bloco Outro_*).
Só dados sintéticos ZZTESTE, origens mockadas e banco *_test. Nenhuma conexão com origem real."""
import logging
import re

import pytest
from sqlalchemy import select, text

from app.bronze.ingestao import ingere
from app.connectors.sqlserver.politicas import politica
from app.core.sql_guard import garante_somente_leitura
from app.gold import visao
from app.ingestion.mapeamentos import canonico
from app.models.camadas import CidadaoSistemas, DadoGold, Endereco, Vinculo
from app.models.controle import Execucao
from app.silver import endereco_saude as es
from app.silver import processamento
from tests.conftest import TEST_URL

RUA = {"CD_RUA": 901, "ENDERECO": "ZZTESTE DAS FLORES", "Logradouro": "ZZTESTE FLORES", "CEP": "14400111",
       "CIDADE": "Franca", "UF": "sp", "Cod_IBGE_Municipio": "3516200", "Descr_Tipo_Logradouro": "Rua"}
BAIRRO = {"CD_BAI": 77, "BAIRRO": "ZZTESTE Centro", "Cidade": "Franca"}


def _cpf(base9: str) -> str:
    d = [int(x) for x in base9]
    for n in (10, 11):
        s = sum(v * (n - i) for i, v in enumerate(d)) % 11
        d.append(0 if s < 2 else 11 - s)
    return "".join(map(str, d))


def _pac(mat, cpf, **o):
    base = {"Matricula": mat, "Nome": f"ZZTESTE PACIENTE {mat}", "NomeSocial": None, "Cpf": cpf, "Cartao_Sus": None,
            "Dt_Nasc": "1980-01-01", "Sexo": "F", "Mae": f"ZZTESTE MAE {mat}", "Pai": None, "Cd_Rua": None, "Nr": None,
            "Cod_Bairro": None, "Cep": None, "Complemento": None, "Falecido": None, "DataObito": None,
            "Normalizado": None, "Matricula_Correta": None, "Dt_Cad": None, "dt_ultimaatual": "2026-02-20T10:00:00",
            "Outra_Rua": None, "Outro_Numero": None, "Outro_Bairro": None, "Outra_Cidade": None, "Outro_Estado": None,
            "Outro_Cep": None, "Cod_Cidade": 999, "Cod_Estado": 35}
    base.update(o)
    return base


@pytest.fixture
def ref():
    es.define_ref({901: es.linha_rua(RUA)}, {77: es.linha_bairro(BAIRRO)})
    yield
    es.define_ref()


# ---------- EN10: Cd_Rua preenchido -> Geral.dbo.Rua + Geral.dbo.Bairro ----------
def test_en10_cd_rua_usa_rua_e_bairro_do_geral(ref):
    p = es.partes(_pac(1, None, Cd_Rua=901, Cod_Bairro=77, Nr="4071", Complemento="ap 12", Cep="14405457"))
    assert p == {"tipo_logradouro": "Rua", "logradouro": "ZZTESTE DAS FLORES", "numero": "4071", "complemento": "ap 12",
                 "bairro": "ZZTESTE Centro", "cidade": "Franca", "uf": "SP", "cep": "14405457",
                 "codigo_ibge": "3516200", "origem_endereco": "geral"}
    assert es.partes(_pac(1, None, Cd_Rua="901", Cod_Bairro=77))["cep"] == "14400111"  # sem Cep: CEP da rua


def test_en10_bloco_outro_nao_entra_quando_cd_rua_resolve(ref):
    p = es.partes(_pac(1, None, Cd_Rua=901, Outra_Rua="ZZTESTE OUTRA", Outra_Cidade="ZZTESTE Cidade"))
    assert p["logradouro"] == "ZZTESTE DAS FLORES" and p["cidade"] == "Franca"


# ---------- EN11: Cd_Rua vazio -> bloco Outro_* ----------
def test_en11_cd_rua_vazio_usa_bloco_outro(ref):
    p = es.partes(_pac(2, None, Outra_Rua="ZZTESTE Av. Brasil", Outro_Numero="10", Outro_Bairro="ZZTESTE Vila",
                       Outra_Cidade="Ribeirão Preto", Outro_Estado="sp", Outro_Cep="14.010-000", Nr="999", Cep="14400000"))
    assert p["origem_endereco"] == "outro"
    assert (p["logradouro"], p["numero"], p["bairro"], p["cidade"], p["uf"], p["cep"]) == (
        "ZZTESTE Av. Brasil", "10", "ZZTESTE Vila", "Ribeirão Preto", "SP", "14010000")


def test_en11_cd_rua_sem_referencia_cai_no_bloco_outro():
    es.define_ref()
    assert es.partes(_pac(2, None, Cd_Rua=901, Outra_Rua="ZZTESTE R"))["origem_endereco"] == "outro"


# ---------- EN12: Cod_Cidade / Cod_Estado nunca aparecem ----------
def test_en12_codigos_internos_fora_da_politica_e_do_texto(ref):
    cols = politica("SAUDE").colunas_select("pacientes")
    assert "Cod_Cidade" not in cols and "Cod_Estado" not in cols
    assert {"Outra_Rua", "Outro_Numero", "Outro_Bairro", "Outra_Cidade", "Outro_Estado", "Outro_Cep"} <= set(cols)
    for d in (_pac(1, None, Cd_Rua=901), _pac(2, None, Outra_Rua="ZZTESTE R"), _pac(3, None, Nr="5")):
        t = es.texto(d) or ""
        assert "999" not in t and "35" not in t.replace("3516200", "")


# ---------- EN1 / EN4 / EN8: formato em 2 linhas ----------
def test_en1_en4_formato_duas_linhas_e_cep_formatado(ref):
    t = es.texto(_pac(1, None, Cd_Rua=901, Cod_Bairro=77, Nr="4071", Complemento="ap 12", Cep="14405457"))
    assert t == "Rua ZZTESTE DAS FLORES, 4071 – ap 12\nZZTESTE Centro · Franca/SP · CEP 14405-457"
    l1 = t.split("\n")[0]
    assert l1.index("ZZTESTE") < l1.index("4071")  # EN4: número nunca antes da rua


def test_en1_tipo_nao_duplica_quando_ja_esta_no_nome():
    assert es.linhas({"tipo_logradouro": "Rua", "logradouro": "RUA ZZTESTE", "numero": None, "complemento": None,
                      "bairro": None, "cidade": None, "uf": None, "cep": None})[0] == "RUA ZZTESTE"


def test_en4_cep_so_digitos_e_invalido_descartado():
    assert es.cep8("14405457") == "14405457" and es.formata_cep("14405457") == "14405-457"
    assert es.cep8("14.405-457") == "14405457"
    assert es.cep8("4071") is None and es.cep8("00000000") is None and es.cep8(True) is None


# ---------- EN14: os três casos com ZZTESTE (o caso do Leandro: "4071, 14405457") ----------
def test_en14_nenhum_dos_dois_mostra_rua_nao_informada_e_numero_depois(ref):
    t = es.texto(_pac(3, None, Nr="4071", Cep="14405457"))
    assert t == f"{es.SEM_RUA}, 4071\nCEP 14405-457"
    assert es.partes(_pac(3, None))["origem_endereco"] == "nenhum" and es.texto(_pac(3, None)) is None


def test_en14_canonico_saude_traz_texto_e_partes(ref):
    c = canonico("SAUDE", "pacientes", _pac(1, None, Cd_Rua=901, Cod_Bairro=77, Nr="1"))
    assert c["endereco"].startswith("Rua ZZTESTE DAS FLORES, 1") and c["endereco_partes"]["origem_endereco"] == "geral"
    assert "endereco_partes" in visao.FORA_DA_GOLD


# ---------- referência do Geral: só SELECT, upsert idempotente ----------
def test_ref_geral_sql_somente_leitura_e_colunas_do_schema():
    for sql in (es.SQL_RUAS, es.SQL_BAIRROS):
        assert garante_somente_leitura(sql) == sql and "*" not in sql
    for col in ("CD_RUA", "ENDERECO", "CEP", "CIDADE", "UF", "Cod_IBGE_Municipio", "Descr_Tipo_Logradouro"):
        assert f"[{col}]" in es.SQL_RUAS
    assert "Cod_Cidade" not in es.SQL_RUAS + es.SQL_BAIRROS


def test_ref_geral_importa_upsert_sem_duplicar(db):
    assert "_test" in TEST_URL.rsplit("/", 1)[-1].split("?")[0]
    r = es.importa(db, [RUA, {"CD_RUA": None}], [BAIRRO])
    assert r == {"ruas": 1, "bairros": 1, "descartadas": 1}
    es.importa(db, [{**RUA, "ENDERECO": "ZZTESTE DAS ROSAS"}], [BAIRRO])
    assert db.execute(text("select count(*), max(logradouro) from ref.geral_rua where cd_rua = 901")).one() == (
        1, "ZZTESTE DAS ROSAS")
    es.importa(db, [RUA], [])


# ---------- EN14 + EN7: ponta a ponta (bronze -> prata -> ouro -> ficha), sem PII no log ----------
def test_en14_ponta_a_ponta_tres_casos_e_sem_pii_no_log(client, tokens, db, caplog):
    es.importa(db, [RUA], [BAIRRO])
    cpfs = [_cpf(b) for b in ("901234567", "902345678", "903456789")]
    linhas = [_pac(990001, cpfs[0], Cd_Rua=901, Cod_Bairro=77, Nr="4071", Cep="14405457"),
              _pac(990002, cpfs[1], Outra_Rua="ZZTESTE Av. Brasil", Outro_Numero="10", Outra_Cidade="Franca",
                   Outro_Estado="SP"),
              _pac(990003, cpfs[2], Nr="4071", Cep="14405457")]
    ex = Execucao(tipo="zzteste_endereco", sistema="SAUDE", status="sucesso", usuario="zz@aion.local")
    db.add(ex)
    db.commit()
    ingere(db, "SAUDE", "pacientes", "Matricula", "dt_ultimaatual", linhas, ex.id)
    db.commit()
    with caplog.at_level(logging.DEBUG):
        processamento.reconstruir(db)
        visao.reconstruir(db)
        db.commit()
    assert not re.search(r"ZZTESTE|14405|4071", caplog.text)  # EN7: nada do endereço nem do nome no log
    esperado = {"990001": ("geral", "Rua ZZTESTE DAS FLORES, 4071", "ZZTESTE Centro · Franca/SP · CEP 14405-457"),
                "990002": ("outro", "ZZTESTE Av. Brasil, 10", "Franca/SP"),
                "990003": ("nenhum", f"{es.SEM_RUA}, 4071", "CEP 14405-457")}
    for mat, (origem, l1, l2) in esperado.items():
        cid = db.scalar(select(Vinculo.cidadao_id).where(Vinculo.sistema == "SAUDE", Vinculo.chave_origem == mat))
        assert cid, mat
        e = db.scalar(select(Endereco).where(Endereco.cidadao_id == cid, Endereco.sistema == "SAUDE"))
        assert e.origem_endereco == origem and e.logradouro == f"{l1}\n{l2}"
        assert db.get(CidadaoSistemas, cid) is not None
        g = db.scalar(select(DadoGold).where(DadoGold.cidadao_id == cid, DadoGold.campo == "endereco"))
        assert g.valor == f"{l1}\n{l2}" and g.sistema == "SAUDE"
        r = client.get(f"/api/gold/cidadaos/{cid}", headers=tokens["admin"])
        assert r.status_code == 200
        ends = [x for x in r.json()["enderecos"] if x["fonte"] == "SAUDE"]
        assert ends[0]["linhas"] == [l1, l2] and ends[0]["origem_endereco"] == origem
        assert "Cod_Cidade" not in r.text and "999" not in str(ends)
        assert mat not in str(r.url)  # sem PII na URL: só o cidadao_id opaco
