# Roda por último: grava bronze IPTU/DIVIDA/FINANCEIRO (e SAUDE). Só dados sintéticos (fixture_tributario).
"""Onda 2 — tributário (TR1–TR8): política com recorte, carga completa, prata de fatos ligada ao CPF e API da ficha."""
from datetime import date

import pytest
from sqlalchemy import func, select

from app.connectors.sqlserver.politicas import TabelaRecusada, politica
from app.core import recursos
from app.core.config import get_settings
from app.core.sql_guard import garante_somente_leitura
from app.ingestion.origem_mock import OrigemMock
from app.models.camadas import DividaAtiva, Identificador, ImovelPossuidor, Lancamento, RegistroBronze
from app.models.controle import Auditoria, Execucao
from app.services import carga_completa as ccomp
from app.services import carga_conector as cc
from app.silver import processamento
from tests.test_zz_carga_conector import Leitor
from tests.test_zzz_carga_completa import Origem


class OrigemTrib(Origem):
    def __init__(self, sistema):
        super().__init__(sistema)
        self.mock = OrigemMock(conjunto="saude+cemiterio+tributario")


class LeitorTrib(Leitor):
    def __init__(self, sistema):
        super().__init__(sistema)
        self.o = OrigemMock(conjunto="saude+cemiterio+tributario")


@pytest.fixture
def origem(monkeypatch):
    o = {s: OrigemTrib(s) for s in ("SAUDE", "CEMITERIO", "IPTU", "DIVIDA", "FINANCEIRO")}
    monkeypatch.setenv("CARGA_COMPLETA_LOTE", "50")
    monkeypatch.setenv("CARGA_COMPLETA_PAUSA_MS", "0")
    monkeypatch.setattr(recursos, "disco", lambda: recursos.Uso(total=100, livre=50))
    get_settings.cache_clear()
    monkeypatch.setattr(ccomp, "leitor_padrao", lambda conector, db: o[cc.CONECTORES[conector][0]])

    def sincrono(ex_id, conector, trava, somente_camadas=False):
        ccomp.rodar(ex_id, conector, trava, leitor=o[cc.CONECTORES[conector][0]], somente_camadas=somente_camadas)
    monkeypatch.setattr(ccomp, "disparar", sincrono)
    yield o
    get_settings.cache_clear()


def _carga(db, conector):
    sis = cc.CONECTORES[conector][0]
    a = cc.executar_amostra(db, "admin@aion.local", conector, leitor=LeitorTrib(sis))
    assert a.status in ("sucesso", "sem_novidades"), a.mensagem
    ex = ccomp.iniciar(db, "admin@aion.local", conector)
    db.expire_all()
    ex = db.get(Execucao, ex.id)
    assert ex.status == "sucesso", ex.mensagem
    return ex


def _cid_sa1(db):
    from app.ingestion import fixture_saude_cemiterio
    cpf = fixture_saude_cemiterio.gerar()["saude"][("SAUDE", "pacientes")][0]["Cpf"]
    return db.scalar(select(Identificador.cidadao_id).where(Identificador.tipo == "cpf", Identificador.valor == cpf))


# ---------------------------------------------------------------- TR1: política

def test_tr1_financeiro_recorte_em_toda_consulta(monkeypatch):
    monkeypatch.setenv("FINANCEIRO_EXERCICIO_MIN", "2025")
    get_settings.cache_clear()
    o, pol, t = Origem("FINANCEIRO"), politica("FINANCEIRO"), "Financeiro_Exercicio"
    ordem = ccomp.ordem("FINANCEIRO", t)
    sqls = [o.montar_contagem(pol, t), *o.montar_verificacao_chave(pol, t, ordem), o.montar_nulos(pol, t, ordem, 10),
            o.montar_pagina(pol, t, ordem, 50, None)[0], o.montar_assinatura(pol, t, "Data_Ultima_Alteracao_Registro"),
            o.montar_select_politica(pol, t)]
    for sql in sqls:
        assert "([Exercicio] >= 2025)" in sql, sql
        assert sql == garante_somente_leitura(sql)
    monkeypatch.delenv("FINANCEIRO_EXERCICIO_MIN")
    get_settings.cache_clear()
    assert f"([Exercicio] >= {date.today().year - 1})" in o.montar_contagem(pol, t)  # padrão: atual e anterior
    assert "WHERE" not in o.montar_contagem(politica("IPTU"), "Cadastro")              # sem recorte nas outras


def test_tr1_colunas_sensiveis_e_tabelas_recusadas():
    for sis, tabela, proibida in (("IPTU", "Cadastro", "NOME"), ("DIVIDA", "Divida", "OBS"),
                                  ("FINANCEIRO", "Financeiro_Exercicio", "Documento")):
        assert proibida not in politica(sis).colunas_select(tabela)
    for sis, tabela in (("IPTU", "Cadastro_Carne_2026"), ("IPTU", "Cadastro_Possuidor_Integracao"),
                        ("DIVIDA", "Divida_Teste_SMS"), ("FINANCEIRO", "Log_Excluidos_Financeiro_Exercicio")):
        with pytest.raises(TabelaRecusada):
            politica(sis).colunas_select(tabela)


def test_tr3_fatos_nao_entram_na_correlacao_de_pessoas():
    assert set(processamento.sistemas_so_fatos()) == {"IPTU", "DIVIDA", "FINANCEIRO", "SIL"}


# ---------------------------------------------------------------- TR2–TR6: carga e prata

def test_tr4_carga_e_prata_tributaria_ligada_ao_cpf(db, origem):
    _carga(db, "saude")
    for conector in ("iptu", "financeiro", "divida"):
        _carga(db, conector)
    cid = _cid_sa1(db)
    assert cid
    # bronze sem colunas sensíveis
    for r in db.scalars(select(RegistroBronze.dados).where(RegistroBronze.sistema_origem.in_(("IPTU", "DIVIDA", "FINANCEIRO")))):
        assert not {"NOME", "OBS", "Documento"} & set(r)
    # possuidores: ativo ligado ao SA1, anterior inativo, PJ e CPF sem cidadão sem vínculo
    pos = {(p.codi, p.tipo_pessoa, p.ativo): p for p in db.scalars(select(ImovelPossuidor))}
    assert pos[("01111010010100", "PF", True)].cidadao_id == cid
    assert pos[("01111010010100", "PF", False)].cidadao_id is None
    assert pos[("02222020020200", "PJ", True)].cidadao_id is None
    assert pos[("03333030030300", "PF", True)].cidadao_id is None
    lan = list(db.scalars(select(Lancamento).order_by(Lancamento.parcela)))
    assert [(x.parcela, x.codi is not None, x.cidadao_id == cid, x.pago) for x in lan] == [
        (1, True, True, True), (2, True, True, False), (3, False, False, False)]
    div = {d.codi: d for d in db.scalars(select(DividaAtiva))}
    assert div["01111010010100"].cidadao_id == cid and not div["01111010010100"].extinta
    assert div["02222020020200"].extinta and div["02222020020200"].cidadao_id is None
    # a correlação de pessoas não criou cidadão a partir de fato tributário
    from app.models.camadas import Vinculo
    assert db.scalar(select(func.count()).select_from(Vinculo).where(
        Vinculo.sistema.in_(("IPTU", "DIVIDA", "FINANCEIRO")))) == 0


def test_tr7_api_tributario_do_cidadao_e_auditada(client, tokens, db, origem):
    _carga(db, "saude")
    for conector in ("iptu", "financeiro", "divida"):
        _carga(db, conector)
    cid = _cid_sa1(db)
    r = client.get(f"/api/gold/cidadaos/{cid}/tributario", headers=tokens["leitura"])
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["totais"]["imoveis"] == 1 and d["totais"]["parcelas_em_aberto"] == 1
    assert d["totais"]["divida_ativa"] == pytest.approx(646.2) and d["totais"]["protestada"] is True
    assert [i["ativo"] for i in d["imoveis"]] == [True] and d["imoveis"][0]["iptu_anual"] == pytest.approx(420.75)
    assert {x["situacao"] for x in d["lancamentos"]} <= {"pago", "vencido", "a_vencer"}
    aud = db.scalar(select(Auditoria).where(Auditoria.acao == "consulta_tributario").order_by(Auditoria.id.desc()))
    assert aud.alvo == f"cidadao:{cid}" and aud.usuario == "leitura@aion.local"
    assert client.get("/api/gold/cidadaos/nao-existe/tributario", headers=tokens["leitura"]).status_code == 404
    assert client.get(f"/api/gold/cidadaos/{cid}/tributario").status_code == 401


def test_tr8_conectores_tributarios_no_catalogo_e_na_agenda(client, tokens):
    from app.services import agenda
    bases = {b["nome"]: b for b in client.get("/api/conectores", headers=tokens["admin"]).json()["bases"]}
    for nome, conj in (("IPTU", "iptu"), ("Divida", "divida"), ("Financeiro", "financeiro")):
        assert bases[nome]["status"] == "implementado" and bases[nome]["conector_id"] == conj
    st = client.get("/api/agenda", headers=tokens["leitura"]).json()
    itens = {i["conector"]: i for i in st["itens"]}
    assert {"iptu", "divida", "financeiro"} <= set(itens) and not itens["financeiro"]["ativo"]
    assert "financeiro" in ccomp.CONECTORES_COMPLETA and agenda.USUARIO == "agenda"


def test_tr1_exercicio_minimo_vazio_no_env_e_nulo(monkeypatch):
    monkeypatch.setenv("FINANCEIRO_EXERCICIO_MIN", "")
    get_settings.cache_clear()
    assert get_settings().financeiro_exercicio_min is None  # como vem do .env.example: o backend precisa subir
    get_settings.cache_clear()


def test_env_example_sobe_as_configuracoes(monkeypatch):
    """O CI sobe o compose com o .env.example: toda variável dele precisa ser aceita pela configuração."""
    import os

    from app.core.config import Settings
    raiz = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    for linha in open(os.path.join(raiz, ".env.example"), encoding="utf-8"):
        linha = linha.strip()
        if linha and not linha.startswith("#") and "=" in linha:
            k, v = linha.split("=", 1)
            monkeypatch.setenv(k, v)
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://x@localhost/x")
    monkeypatch.setenv("JWT_SECRET", "segredo-de-teste-com-32-caracteres!!")
    Settings()  # não pode levantar ValidationError
