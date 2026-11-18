"""Titulares de uma sepultura: ativos e inativos, atual primeiro, sem documento.

A lista sai da bronze CEMITERIO.Titulares (flag Ativo S/N). Inclusao é código, não data.
CAD_CEMITE e Ocorrencia_Sepultura não entram. Só dados fictícios.
"""
import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import delete

from app.core.security import hash_senha
from app.models.camadas import Cidadao, RegistroBronze
from app.models.controle import Execucao, Usuario, UsuarioSecretaria
from tests.conftest import SENHA

CID = "sepultura-titulares-1"
CPF_ATUAL = "52998224725"
CPF_ANTIGO = "11144477735"
CPF_OUTRA = "39053344705"
DOC_CADASTRO = "99988877766"
INCLUSAO_QUE_PARECE_DATA = "2019-04-04"
NOME_ATUAL = "Zezinho Atual Ficticio"
NOME_ANTIGO = "AAA Inativo Ficticio"
NOME_VERSAO_VELHA = "Versao Substituida Ficticia"
NOME_OUTRA = "Outra Sepultura Ficticia"
NOME_CADASTRO = "So No Cadastro Ficticio"


def _limpa(db):
    db.execute(delete(RegistroBronze).where(RegistroBronze.sistema_origem == "CEMITERIO",
                                            RegistroBronze.id_origem.like("sep-%")))
    db.commit()


@pytest.fixture
def sepulturas(db):
    """A suíte compartilha o banco: a bronze fictícia não pode sobrar e inflar a reconciliação do cemitério."""
    _prepara(db)
    yield
    _limpa(db)


def _prepara(db):
    if db.get(Cidadao, CID) is None:
        db.add(Cidadao(cidadao_id=CID, nome="Pessoa Ficticia Sepultura", data_nascimento=date(1970, 1, 1)))
        db.flush()
    _limpa(db)
    ex = Execucao(tipo="teste", sistema="CEMITERIO", status="sucesso", modo="completa")
    db.add(ex)
    db.flush()
    linhas = [
        ("sep-s1-1", "h-velha", "Titulares", {"Cod_Cemiterio": 77, "Quadra": "QZ77", "Sepultura": "SZ77", "Sequencia": 1,
                                              "Titular": NOME_VERSAO_VELHA, "CPF": CPF_ATUAL, "Endereco": "RUA OCULTA",
                                              "Ativo": "N", "Inclusao": "C"}),
        ("sep-s1-1", "h-atual", "Titulares", {"Cod_Cemiterio": "77", "Quadra": "QZ77", "Sepultura": "SZ77", "Sequencia": 1,
                                              "Titular": NOME_ATUAL, "CPF": CPF_ATUAL, "Endereco": "RUA OCULTA",
                                              "Ativo": "S", "Inclusao": "C"}),
        ("sep-s1-2", "h-antigo", "Titulares", {"Cod_Cemiterio": "77", "Quadra": "QZ77", "Sepultura": "SZ77", "Sequencia": 2,
                                               "Titular": NOME_ANTIGO, "CPF": CPF_ANTIGO, "Ativo": "N",
                                               "Inclusao": INCLUSAO_QUE_PARECE_DATA}),
        ("sep-outra", "h-outra", "Titulares", {"Cod_Cemiterio": "77", "Quadra": "QZ77", "Sepultura": "SZ88", "Sequencia": 1,
                                               "Titular": NOME_OUTRA, "CPF": CPF_OUTRA, "Ativo": "S", "Inclusao": "C"}),
        ("sep-cad", "h-cad", "CAD_CEMITE", {"COD_CEMITERIO": "77", "QUADRA": "QZ77", "SEPULTURA": "SZ77",
                                            "TITULAR": NOME_CADASTRO, "TIPO_DOC": "CPF", "DOCUMENTO": DOC_CADASTRO}),
        ("sep-oc", "h-oc", "Ocorrencia_Sepultura", {"Cod_Cemiterio": "77", "Quadra": "QZ77", "Sepultura": "SZ77",
                                                    "Seq_Ocorrencia": 1, "Dt_Inclusao": "2020-01-01"}),
    ]
    for origem, hash_, tab, dados in linhas:
        db.add(RegistroBronze(sistema_origem="CEMITERIO", tabela=tab, id_origem=origem, dados=dados, hash=hash_,
                              execucao_id=ex.id))
    db.commit()


def _usuario(db, client, perfil, secretaria):
    email = f"zzteste-{uuid.uuid4().hex[:8]}@aion.local"
    u = Usuario(email=email, nome=f"ZZTESTE {perfil}", perfil=perfil, senha_hash=hash_senha(SENHA))
    db.add(u)
    db.flush()
    db.add(UsuarioSecretaria(usuario_id=u.id, secretaria=secretaria, vigencia_inicio=date.today() - timedelta(days=30),
                             cadastrado_por="admin@aion.local"))
    db.commit()
    r = client.post("/api/auth/login", json={"usuario": email, "senha": SENHA})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_lista_ativos_e_inativos_atual_primeiro_sem_cpf(client, tokens, sepulturas):
    r = client.get(f"/api/gold/cidadaos/{CID}/cemiterio/titulares",
                   params={"cod": "77", "quadra": "QZ77", "sepultura": "SZ77"}, headers=tokens["leitura"])
    assert r.status_code == 200
    itens = r.json()["titulares"]
    assert [t["nome"] for t in itens] == [NOME_ATUAL, NOME_ANTIGO]
    assert itens[0]["atual"] is True and itens[0]["situacao"] == "Ativo"
    assert itens[1]["atual"] is False and itens[1]["situacao"] == "Inativo"
    assert all(set(t) == {"nome", "situacao", "atual"} for t in itens)
    texto = r.text
    for proibido in (CPF_ATUAL, CPF_ANTIGO, CPF_OUTRA, DOC_CADASTRO, INCLUSAO_QUE_PARECE_DATA, "2020-01-01",
                     "RUA OCULTA", NOME_VERSAO_VELHA, NOME_OUTRA, NOME_CADASTRO, "cpfcnpjresponsavel", "DOCUMENTO"):
        assert proibido not in texto
    assert "cpf" not in texto.lower()


def test_perfil_sem_secretaria_do_cemiterio_recebe_403(client, db, monkeypatch, sepulturas):
    monkeypatch.setenv("SIGPMF_RBAC_SECRETARIA", "true")
    sem_acesso = _usuario(db, client, "admin", "SAUDE")
    url = f"/api/gold/cidadaos/{CID}/cemiterio/titulares"
    params = {"cod": "77", "quadra": "QZ77", "sepultura": "SZ77"}
    negado = client.get(url, params=params, headers=sem_acesso)
    assert negado.status_code == 403
    assert NOME_ATUAL not in negado.text and CPF_ATUAL not in negado.text and "cpf" not in negado.text.lower()
    assert client.get(f"/api/gold/cidadaos/{CID}/cemiterio", headers=sem_acesso).status_code == 403
    com_acesso = _usuario(db, client, "leitura", "SERVICOS_URBANOS")
    liberado = client.get(url, params=params, headers=com_acesso)
    assert liberado.status_code == 200
    assert [t["nome"] for t in liberado.json()["titulares"]] == [NOME_ATUAL, NOME_ANTIGO]
    assert CPF_ATUAL not in liberado.text
