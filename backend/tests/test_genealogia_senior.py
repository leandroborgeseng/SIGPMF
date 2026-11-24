"""Folha nominal do Senior e árvore genealógica. Só pessoas e CPFs fictícios (DV calculado, série 800)."""
import inspect
import pathlib
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import delete, select, text

from app.bronze.ingestao import ingere
from app.connectors.senior.conector import ROTULO, _marca
from app.connectors.sqlserver.connector import ConfigSqlServer, SqlServerConnector
from app.connectors.sqlserver.politicas import SENIOR
from app.core.sql_guard import garante_somente_leitura
from app.genealogia.arvore import ISENTO_K_ANONIMATO, arvore_de, montar
from app.genealogia.confianca import CRITERIO_FRACO, pontua
from app.genealogia.indice import Indice, cpf_valido
from app.genealogia.leitura import ArvoreIndisponivel, executa
from app.genealogia.materializar import materializar
from app.genealogia.provedores import REGISTRO, Aresta
from app.identity.correlacao import cidadao_id_por_cpf, normaliza_nome
from app.identity.cpf import digitos_verificadores, mascara
from app.models.camadas import (
    Cidadao,
    CidadaoSistemas,
    DadoGold,
    Endereco,
    EscolaAluno,
    EscolaResponsavel,
    Identificador,
    ImovelPossuidor,
    SeniorDependente,
    SeniorServidor,
    Vinculo,
)
from app.models.controle import Auditoria, Execucao
from app.models.prata_modelo import CidadaoEmpresa, Empresa
from app.silver import senior as senior_prata

RAIZ = pathlib.Path(__file__).resolve().parents[1]


def cpf_ficticio(n: int) -> str:
    base = f"{800000000 + n:09d}"
    return base + digitos_verificadores(base)


def test_senior_sql_e_so_select_e_marca_folha_fora_do_sql():
    con = SqlServerConnector(ConfigSqlServer(None, 1433, None, None, None, 2, 10), nome="senior")
    for tabela in ("r034fun", "r036dep"):
        for sql in (
            con.montar_select_politica(SENIOR, tabela, limite=5),
            con.montar_pagina(SENIOR, tabela, SENIOR.ordem[tabela], 10, None)[0],
            con.montar_contagem(SENIOR, tabela),
        ):
            assert sql.lstrip().upper().startswith("SELECT")
            garante_somente_leitura(sql)
            assert "salario" not in sql.lower() and "folha_nominal" not in sql.lower()
    assert _marca("dbo.r034fun", [{"nomfun": "ZZ"}])[0]["folha_nominal"] == ROTULO["r034fun"] == "Servidor"
    assert _marca("r036dep", [{}])[0]["folha_nominal"] == "Dependente"
    assert "conecta_guardado" in inspect.getsource(con._conectar)
    fonte_conector = (RAIZ / "app" / "connectors" / "senior" / "conector.py").read_text(encoding="utf-8")
    assert "super().ler_amostra" in fonte_conector and "super().ler_pagina" in fonte_conector


def test_confianca_fraca_exata_bonus_e_teto():
    assert "0,70" in CRITERIO_FRACO and "0,99" in CRITERIO_FRACO
    exata = pontua("Helena Unica", "helena   única", nasc_filho=None, nasc_candidato=None,
                   cep_filho=None, log_filho=None, cep_candidato=None, log_candidato=None)
    assert exata and exata.exato and exata.score == 0.70 and not exata.bonus
    com_idade = pontua("Helena Unica", "HELENA UNICA", nasc_filho=date(2000, 6, 1), nasc_candidato=date(1970, 1, 15),
                       cep_filho=None, log_filho=None, cep_candidato=None, log_candidato=None)
    assert com_idade and com_idade.score == 0.85 and com_idade.bonus
    teto = pontua("Helena Unica", "Helena Unica", nasc_filho=date(2000, 6, 1), nasc_candidato=date(1970, 1, 15),
                  cep_filho="14400-999", log_filho="Rua das Acácias", cep_candidato="14400999",
                  log_candidato="RUA DAS ACACIAS")
    assert teto and teto.score == 0.99 and teto.score < 1
    fora = pontua("Helena Unica", "Outra Pessoa", nasc_filho=None, nasc_candidato=None,
                  cep_filho=None, log_filho=None, cep_candidato=None, log_candidato=None)
    assert fora is None
    cedo = pontua("Helena Unica", "Helena Unica", nasc_filho=date(1972, 1, 1), nasc_candidato=date(1970, 1, 1),
                  cep_filho=None, log_filho=None, cep_candidato=None, log_candidato=None)
    assert cedo and cedo.score == 0.70


def test_arvore_em_memoria_dois_niveis_unifica_cpf_e_corta_ciclo():
    ind = Indice()
    avo = ind.garante(nome="Avo", cpf=cpf_ficticio(1), origem="t-avo")
    mae = ind.garante(nome="Mae", cpf=cpf_ficticio(2), origem="t-mae")
    ego = ind.garante(nome="Ego", cpf=cpf_ficticio(3), origem="t-ego")
    de_novo = ind.garante(nome="Ego de novo", cpf=cpf_ficticio(3), cidadao_id="cid-ego", origem="t-ego-2")
    assert de_novo == ego
    filho = ind.garante(nome="Filho", cpf=cpf_ficticio(4), origem="t-filho")
    neto = ind.garante(nome="Neto", cpf=cpf_ficticio(5), origem="t-neto")
    arestas = [
        Aresta(ego, mae, "ascendente", "SENIOR", "forte", "mãe", 1.0, "r036dep"),
        Aresta(mae, avo, "ascendente", "EDUCACAO", "forte", "mãe", 1.0, "pes_idFiliacaoMae"),
        Aresta(ego, filho, "descendente", "SENIOR", "forte", "filho(a)", 1.0, "r036dep"),
        Aresta(filho, neto, "descendente", "SENIOR", "forte", "filho(a)", 1.0, "r036dep"),
        Aresta(ego, mae, "ascendente", "SAUDE", "fraco", "mãe", 0.7, "nome"),
    ]
    arv = arvore_de(ego, ind, arestas)
    assert [n for n, c, _v, _h in arv["ascendentes"]] == [1, 2]
    assert [ind.no(c).nome for _n, c, _v, _h in arv["ascendentes"]] == ["Mae", "Avo"]
    assert [ind.no(c).nome for _n, c, _v, _h in arv["descendentes"]] == ["Filho", "Neto"]
    mae_hops = arv["ascendentes"][0][3]
    assert {a.base for a in mae_hops} == {"SENIOR"}  # a fraca some porque já há forte
    # ciclo A↔B não repete nem estoura
    a = ind.garante(nome="Ciclo A", origem="ciclo-a")
    b = ind.garante(nome="Ciclo B", origem="ciclo-b")
    ciclo = arvore_de(a, ind, [
        Aresta(a, b, "ascendente", "SENIOR", "forte", "pai", 1.0, "x"),
        Aresta(b, a, "ascendente", "SENIOR", "forte", "pai", 1.0, "y"),
    ])
    assert [c for _n, c, _v, _h in ciclo["ascendentes"]] == [b]


def test_carga_senior_e_arvore_no_banco(db):
    cpf = {p: cpf_ficticio(n) for n, p in enumerate(("avo", "mae", "ego", "filho", "neto", "conjuge"), start=1)}
    quando = "2024-06-01T15:00:00"
    nomes = {"avo": "ZZGENEALOGIA AVO LINA", "mae": "ZZGENEALOGIA MAE ROSA", "ego": "ZZGENEALOGIA EGO CAIO",
             "filho": "ZZGENEALOGIA FILHO DAVI", "neto": "ZZGENEALOGIA NETO ENZO", "conjuge": "ZZGENEALOGIA CONJUGE BIA"}
    nasc = {"avo": "1950-01-10", "mae": "1975-04-02", "ego": "2000-08-08", "filho": "2020-02-02", "neto": "2040-03-03",
            "conjuge": "2001-01-01"}
    try:
        ex = Execucao(tipo="zzgenealogia", sistema="SENIOR", status="sucesso", usuario="zz@aion.local")
        db.add(ex)
        db.flush()
        servidores = [{"numemp": 1, "tipcol": 1, "numcad": i + 1, "nomfun": nomes[p], "numcpf": cpf[p],
                       "datnas": nasc[p], "datadm": "2010-01-01", "sitafa": 1, "datafa": quando}
                      for i, p in enumerate(("avo", "mae", "ego", "filho", "neto", "conjuge"))]
        deps = [
            {"numemp": 1, "tipcol": 1, "numcad": 2, "coddep": 1, "nomdep": nomes["avo"], "numcpf": cpf["avo"],
             "datnas": nasc["avo"], "grapar": 9},
            {"numemp": 1, "tipcol": 1, "numcad": 3, "coddep": 1, "nomdep": nomes["mae"], "numcpf": cpf["mae"],
             "datnas": nasc["mae"], "grapar": 9},
            {"numemp": 1, "tipcol": 1, "numcad": 3, "coddep": 2, "nomdep": nomes["filho"], "numcpf": cpf["filho"],
             "datnas": nasc["filho"], "grapar": 3},
            {"numemp": 1, "tipcol": 1, "numcad": 4, "coddep": 1, "nomdep": nomes["neto"], "numcpf": cpf["neto"],
             "datnas": nasc["neto"], "grapar": 3},
            {"numemp": 1, "tipcol": 1, "numcad": 3, "coddep": 3, "nomdep": nomes["conjuge"], "numcpf": cpf["conjuge"],
             "datnas": nasc["conjuge"], "grapar": 1},
        ]
        ingere(db, "SENIOR", "r034fun", ("numemp", "tipcol", "numcad"), "datafa", servidores, ex.id, confirmar=False)
        ingere(db, "SENIOR", "r036dep", ("numemp", "tipcol", "numcad", "coddep"), None, deps, ex.id, confirmar=False)
        pessoas = [
            {"pes_id": "zz-avo", "pes_nome": nomes["avo"], "pes_dataNascimento": nasc["avo"]},
            {"pes_id": "zz-mae", "pes_nome": nomes["mae"], "pes_dataNascimento": nasc["mae"], "pes_idFiliacaoMae": "zz-avo"},
            {"pes_id": "zz-ego", "pes_nome": nomes["ego"], "pes_dataNascimento": nasc["ego"], "pes_idFiliacaoMae": "zz-mae"},
            {"pes_id": "zz-filho", "pes_nome": nomes["filho"], "pes_dataNascimento": nasc["filho"], "pes_idFiliacaoMae": "zz-ego"},
            {"pes_id": "zz-neto", "pes_nome": nomes["neto"], "pes_dataNascimento": nasc["neto"], "pes_idFiliacaoMae": "zz-filho"},
        ]
        docs = [{"pes_id": f"zz-{p}", "tdo_classe": 1, "psd_numero": cpf[p], "psd_situacao": 1}
                for p in ("avo", "mae", "ego", "filho", "neto")]
        ingere(db, "CORESSO", "PES_Pessoa", "pes_id", None, pessoas, ex.id, confirmar=False)
        ingere(db, "CORESSO", "PES_PessoaDocumento", ("pes_id", "tdo_classe"), None, docs, ex.id, confirmar=False)
        n1 = senior_prata.projetar(db)
        n2 = senior_prata.projetar(db)
        assert n1["servidores"] == n2["servidores"] == 6
        assert n1["dependentes"] == 5
        assert db.scalar(select(SeniorServidor).where(SeniorServidor.cpf == cpf["ego"])).fonte_atualizado_em == datetime(2024, 6, 1, 15, tzinfo=UTC)
        dep = db.scalar(select(SeniorDependente).where(SeniorDependente.cpf == cpf["mae"]))
        assert dep.sistema == "SENIOR" and dep.cpf_titular == cpf["ego"] and dep.papel == "ascendente"
        assert dep.fonte_atualizado_em == datetime(2024, 6, 1, 15, tzinfo=UTC)
        ouro = db.scalar(select(DadoGold).where(DadoGold.cidadao_id == dep.cidadao_titular_id, DadoGold.campo == "folha_nominal"))
        assert ouro and ouro.sistema == "SENIOR" and "Servidor" in ouro.valor and ouro.fonte_atualizado_em is not None
        cid = {p: cidadao_id_por_cpf(cpf[p]) for p in cpf}
        db.add(EscolaAluno(alu_id=91001, nome=nomes["ego"], cpf=cpf["ego"], cidadao_id=cid["ego"], status_vinculo="vinculado"))
        db.add(EscolaResponsavel(alu_id=91001, alr_id=1, tra_id=1, tipo="Mãe", nome=nomes["mae"], cpf=cpf["mae"], excluido=False))
        db.add(ImovelPossuidor(codi="ZZGEN1", documento=cpf["filho"], tipo_pessoa="PF", cidadao_id=cid["filho"], ativo=True))
        db.execute(text("INSERT INTO bronze.sil_imoveis_fisico (idfisico, cpfcnpjresponsavel) VALUES (910001, :cpf)"),
                   {"cpf": cpf["neto"]})
        bronze_id = db.scalar(select(Vinculo.bronze_id).where(Vinculo.cidadao_id == cid["ego"], Vinculo.sistema == "SENIOR"))
        db.add(Vinculo(cidadao_id=cid["ego"], sistema="CEMITERIO", tabela="Titulares", chave_origem="zz-gen-tumulo",
                       bronze_id=bronze_id, metodo="cpf", confianca=1.0, atributos=["cpf"]))
        emp = Empresa(razao_social="Padaria Ficticia ZZ", fonte_inscricao="ZZ", inscricao_municipal="zz-gen")
        db.add(emp)
        db.flush()
        db.add(CidadaoEmpresa(cidadao_id=cid["mae"], empresa_id=emp.id, papel="socio", sistema="ISS",
                              tabela="zz", chave_hash="zzgenealogiaempresa"))
        db.flush()
        ingere(db, "SAUDE", "pacientes", "Matricula", None, [
            {"Matricula": "zz-ego-mae", "Nome": nomes["ego"], "Cpf": cpf["ego"], "Mae": nomes["mae"]},
        ], ex.id, confirmar=False)
        assert materializar(db)["arestas"] > 0
        arv = montar(db, cid["ego"])
        assert ISENTO_K_ANONIMATO is True and arv["isento_k_anonimato"] is True
        assert "0,70" in arv["criterio_vinculo_fraco"]
        assert {p["codigo"] for p in arv["provedores"] if not p["disponivel"]} >= {"CADUNICO", "BU_EDUCACAO"}
        assert all(p["motivo"] == "sem acesso" for p in arv["provedores"] if p["codigo"] in ("CADUNICO", "BU_EDUCACAO"))
        assert [p["pessoa"]["nome"] for p in arv["ascendentes"]] == [nomes["mae"], nomes["avo"]]
        assert [p["nivel"] for p in arv["ascendentes"]] == [1, 2]
        assert arv["ascendentes"][1]["via"] == nomes["mae"]
        assert [p["pessoa"]["nome"] for p in arv["descendentes"]] == [nomes["filho"], nomes["neto"]]
        assert [p["nivel"] for p in arv["descendentes"]] == [1, 2]
        ids = [p["pessoa"]["cidadao_id"] for p in arv["ascendentes"] + arv["descendentes"] + arv["laterais"]]
        assert len(ids) == len(set(ids)) and all(ids)
        mae = arv["ascendentes"][0]
        assert {a["base"] for a in mae["arestas"]} >= {"SENIOR", "EDUCACAO"}
        assert all(a["forca"] == "forte" and a["confianca"] == 1.0 for a in mae["arestas"])
        assert any(a["fonte_atualizado_em"] for a in mae["arestas"])
        assert arv["descendentes"][0]["pessoa"]["indicadores"]["imovel"]["tem"] is True
        assert "IPTU" in arv["descendentes"][0]["pessoa"]["indicadores"]["imovel"]["fontes"]
        assert arv["descendentes"][0]["pessoa"]["indicadores"]["imovel"]["ficha"].endswith("#area-imoveis")
        assert arv["descendentes"][1]["pessoa"]["indicadores"]["imovel"]["fontes"] == ["SIL"]
        assert arv["foco"]["indicadores"]["tumulo"]["tem"] is True
        assert arv["foco"]["indicadores"]["tumulo"]["ficha"].endswith("#area-tumulos")
        assert mae["pessoa"]["indicadores"]["empresa"]["tem"] is True
        assert "ISS" in mae["pessoa"]["indicadores"]["empresa"]["fontes"]
        assert mae["pessoa"]["indicadores"]["empresa"]["ficha"].endswith("#area-empresas")
        assert arv["laterais"] and arv["laterais"][0]["pessoa"]["nome"] == nomes["conjuge"]
        assert arv["foco"]["cpf"] == mascara(cpf["ego"]) and cpf["ego"] not in str(arv)
        # vínculo fraco: nome de mãe, com e sem bônus, e o caso ambíguo que não gera aresta
        helena = "ZZGENEALOGIA HELENA UNICA 7F3A"
        cid_h = cidadao_id_por_cpf(cpf_ficticio(20))
        db.add(Cidadao(cidadao_id=cid_h, nome=helena, data_nascimento=date(1970, 1, 15)))
        db.add(Identificador(cidadao_id=cid_h, tipo="cpf", valor=cpf_ficticio(20)))
        db.add(Endereco(cidadao_id=cid_h, logradouro="RUA DAS ACACIAS FICTICIAS", cep="14400999", sistema="SAUDE",
                        mais_recente=True))
        seco = cidadao_id_por_cpf(cpf_ficticio(21))
        bonus = cidadao_id_por_cpf(cpf_ficticio(22))
        db.add(Cidadao(cidadao_id=seco, nome="ZZGENEALOGIA FILHO HELENA SECO"))
        db.add(Identificador(cidadao_id=seco, tipo="cpf", valor=cpf_ficticio(21)))
        db.add(Cidadao(cidadao_id=bonus, nome="ZZGENEALOGIA FILHO HELENA BONUS", data_nascimento=date(2000, 6, 1)))
        db.add(Identificador(cidadao_id=bonus, tipo="cpf", valor=cpf_ficticio(22)))
        for n in (30, 31):
            c = cidadao_id_por_cpf(cpf_ficticio(n))
            db.add(Cidadao(cidadao_id=c, nome="ZZGENEALOGIA OLIVIA DUPLICADA"))
            db.add(Identificador(cidadao_id=c, tipo="cpf", valor=cpf_ficticio(n)))
        ambiguo = cidadao_id_por_cpf(cpf_ficticio(32))
        db.add(Cidadao(cidadao_id=ambiguo, nome="ZZGENEALOGIA FILHO OLIVIA"))
        db.add(Identificador(cidadao_id=ambiguo, tipo="cpf", valor=cpf_ficticio(32)))
        db.flush()
        ingere(db, "SAUDE", "pacientes", "Matricula", None, [
            {"Matricula": "zz-seco", "Nome": "ZZGENEALOGIA FILHO HELENA SECO", "Cpf": cpf_ficticio(21), "Mae": helena},
            {"Matricula": "zz-bonus", "Nome": "ZZGENEALOGIA FILHO HELENA BONUS", "Cpf": cpf_ficticio(22),
             "Dt_Nasc": "2000-06-01", "Mae": helena, "Cep": "14400-999", "Outra_Rua": "Rua das Acácias Fictícias"},
            {"Matricula": "zz-ambiguo", "Nome": "ZZGENEALOGIA FILHO OLIVIA", "Cpf": cpf_ficticio(32),
             "Mae": "ZZGENEALOGIA OLIVIA DUPLICADA"},
        ], ex.id, confirmar=False)
        primeira = materializar(db)["arestas"]
        assert materializar(db)["arestas"] == primeira
        seco_arv = montar(db, seco)
        assert len(seco_arv["ascendentes"]) == 1
        a = seco_arv["ascendentes"][0]["arestas"][0]
        assert a["base"] == "SAUDE" and a["forca"] == "fraco" and a["provavel"] is True and a["confianca"] == 0.70
        bonus_arv = montar(db, bonus)
        b = bonus_arv["ascendentes"][0]["arestas"][0]
        assert b["confianca"] == 0.99 and b["forca"] == "fraco" and "idade" in b["criterio"] and "CEP" in b["criterio"]
        assert montar(db, ambiguo)["ascendentes"] == []
        codigos = {p.codigo: p for p in REGISTRO}
        assert codigos["CADUNICO"].arestas(db, Indice()) == [] and codigos["BU_EDUCACAO"].disponivel(db) is False
        texto = (RAIZ / "app" / "genealogia" / "arvore.py").read_text(encoding="utf-8")
        texto += (RAIZ / "app" / "silver" / "senior.py").read_text(encoding="utf-8")
        assert "suprime(" not in texto and "k_do_cubo" not in texto
        mig = (RAIZ / "alembic" / "versions" / "0051_senior_genealogia.py").read_text(encoding="utf-8").upper()
        assert "DROP " not in mig and "DELETE " not in mig and "TRUNCATE" not in mig
        assert "CREATE TABLE IF NOT EXISTS" in mig
        mig52 = (RAIZ / "alembic" / "versions" / "0052_genealogia_arestas.py").read_text(encoding="utf-8").upper()
        assert "DROP " not in mig52 and "DELETE " not in mig52 and "TRUNCATE" not in mig52
        assert "CREATE TABLE IF NOT EXISTS" in mig52 and "CONCURRENTLY" in mig52
        fonte = (RAIZ / "app" / "genealogia" / "arvore.py").read_text(encoding="utf-8")
        assert "regexp_replace" not in fonte and "bronze.cpf_so_digitos" in fonte
    finally:
        db.rollback()


def test_rota_genealogia_indisponivel_e_depois_vazia(client, tokens, db):
    cid = "zz-genealogia-rota"
    try:
        db.add(Cidadao(cidadao_id=cid, nome="ZZGENEALOGIA ROTA"))
        db.add(CidadaoSistemas(cidadao_id=cid, nome="ZZGENEALOGIA ROTA", sistemas=[], dominios=[]))
        db.execute(text("""
            INSERT INTO gold.genealogia_estado (id, geracao, populado_em)
            VALUES (1, 0, NULL)
            ON CONFLICT (id) DO UPDATE SET populado_em = NULL
        """))
        db.commit()
        r = client.get(f"/api/gold/cidadaos/{cid}/genealogia", headers=tokens["leitura"])
        assert r.status_code == 503, r.text
        corpo = r.json()
        assert corpo["estado"] == "indisponivel" and corpo["motivo"] == "nao_materializada"
        assert "gold.genealogia_arestas" in corpo["mensagem"]
        materializar(db)
        db.commit()
        r = client.get(f"/api/gold/cidadaos/{cid}/genealogia", headers=tokens["leitura"])
        assert r.status_code == 200, r.text
        corpo = r.json()
        assert corpo["foco"]["nome"] == "ZZGENEALOGIA ROTA"
        assert corpo["ascendentes"] == [] and corpo["descendentes"] == []
        assert any(p["codigo"] == "CADUNICO" and p["disponivel"] is False for p in corpo["provedores"])
        assert db.scalar(select(Auditoria).where(Auditoria.acao == "consulta_genealogia", Auditoria.alvo == f"cidadao:{cid}"))
        assert client.get(f"/api/gold/cidadaos/{cid}/genealogia").status_code == 401
    finally:
        db.rollback()
        db.execute(delete(CidadaoSistemas).where(CidadaoSistemas.cidadao_id == cid))
        db.execute(delete(Cidadao).where(Cidadao.cidadao_id == cid))
        db.commit()


def test_nome_e_cpf_sql_batem_com_python(db):
    for nome in ("helena   única", "Rua das Acácias", "JOÃO-PEDRO", "AÇAÍ", None, ""):
        sql = db.scalar(text("SELECT gold.nome_norm(:n)"), {"n": nome}) or ""
        assert sql == normaliza_nome(nome)
    valido = cpf_ficticio(7)
    assert db.scalar(text("SELECT gold.cpf_valido_digitos(:c)"), {"c": valido}) == cpf_valido(valido) == valido
    assert db.scalar(text("SELECT gold.cpf_valido_digitos(:c)"), {"c": "111.111.111-11"}) is None
    assert cpf_valido("111.111.111-11") is None


def test_timeout_de_consulta_vira_indisponivel(db):
    try:
        with pytest.raises(ArvoreIndisponivel) as exc:
            executa(db, "SELECT pg_sleep(0.4)", {}, timeout="50ms")
        assert exc.value.motivo == "timeout" and "tempo" in exc.value.mensagem
    finally:
        db.rollback()
