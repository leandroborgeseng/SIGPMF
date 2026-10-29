"""PR-04 allowlist reforçada (A1–A3). Só nomes sintéticos; nenhuma conexão real."""
import pytest
from sqlalchemy import text

from app.connectors.sqlserver import politicas as P
from app.connectors.sqlserver.politicas import POLITICAS, BaseRecusada, Politica, TabelaRecusada

PADROES_BACKUP = ["Financeiro_Exercicio_Bkp_2023", "AAA_NAO_UTILIZADO_Pessoas", "Rendas_bkp_2019", "Tabela_BKP_X",
                  "Apagar_Contribuinte", "2APAGAR_x", "Pessoas_bak", "Pessoas_backup", "Copia_Pessoas", "Pessoas_Copia",
                  "temp_carga", "Pessoas_Antes", "Lancamentos_Anteriores", "Pessoas_20240101"]


@pytest.mark.parametrize("sistema", sorted(POLITICAS))
@pytest.mark.parametrize("tabela", PADROES_BACKUP)
def test_a1_padrao_backup_recusado_em_toda_politica(sistema, tabela):
    with pytest.raises(TabelaRecusada) as e:
        POLITICAS[sistema].autoriza(tabela)
    assert e.value.codigo == "tabela_padrao_recusado"


@pytest.mark.parametrize("sistema", sorted(POLITICAS))
def test_a1_tabelas_permitidas_atuais_continuam_permitidas(sistema):
    pol = POLITICAS[sistema]
    for t in list(pol.colunas) + list(pol.apoio):
        pol.autoriza(t)  # regressão: os padrões novos não derrubam nenhuma tabela já liberada


def test_a1_listagem_recusa_e_audita(db):
    pol = POLITICAS["IPTU"]
    perm = next(iter(pol.colunas))
    ok, rec = P.classifica_listagem(pol, [perm, "Financeiro_Exercicio_Bkp_2023", "ZZ_desconhecida"])
    assert ok == [perm]
    assert dict(rec) == {"Financeiro_Exercicio_Bkp_2023": "tabela_padrao_recusado",
                         "ZZ_desconhecida": "tabela_fora_da_lista"}
    antes = db.execute(text("select count(*) from controle.auditoria where acao='tabela_recusada'")).scalar()
    assert P.audita_recusas(db, "zzteste@aion.local", "IPTU", rec) == 2
    depois = db.execute(text("select count(*) from controle.auditoria where acao='tabela_recusada'")).scalar()
    assert depois - antes == 2
    alvo = db.execute(text("select detalhe from controle.auditoria where alvo='IPTU.ZZ_desconhecida'")).scalar()
    assert alvo == {"codigo": "tabela_fora_da_lista"}


@pytest.mark.parametrize("base", sorted(P.BASES_NUNCA))
def test_a2_bancos_de_descarte_e_internet_nunca_entram(base):
    assert len(P.BASES_DESCARTE) == 20 and "Internet" in P.BASES_NUNCA
    for variante in (base, base.lower(), base.upper(), f"[{base}]"):
        with pytest.raises(BaseRecusada) as e:
            P.autoriza_base(variante)
        assert e.value.codigo == "base_descartada"
    with pytest.raises(BaseRecusada):
        Politica(sistema="ZZTESTE", base=base, colunas={"t": ("a",)})
    with pytest.raises(BaseRecusada):
        POLITICAS["IPTU"].autoriza(f"{base}.dbo.{next(iter(POLITICAS['IPTU'].colunas))}")


def test_a2_conector_nao_conecta_em_banco_de_descarte(monkeypatch):
    import sys
    import types

    from app.connectors.sqlserver.connector import ConfigSqlServer, SqlServerConnector
    chamado = []
    monkeypatch.setitem(sys.modules, "pymssql", types.SimpleNamespace(connect=lambda **k: chamado.append(1)))
    cfg = ConfigSqlServer("h", 1433, "Internet", "u", "p", 5, 10)
    with pytest.raises(BaseRecusada):
        SqlServerConnector(cfg)._conectar()
    assert chamado == []


def test_a2_nenhuma_politica_aponta_para_base_proibida():
    assert not {p.base.lower() for p in POLITICAS.values()} & {b.lower() for b in P.BASES_NUNCA}


@pytest.mark.parametrize("col", ["Senha", "senha_web", "UserPassword", "api_token", "Renda", "Raca", "Cor",
                                 "Salario_Base", "Religiao", "Etnia", "CID10", "Deficiencia"])
def test_a3_coluna_proibida_recusada_dentro_de_tabela_permitida(col):
    pol = Politica(sistema="ZZTESTE", base="ZZTESTE_test", colunas={"zz_pessoas": ("Codigo", col)})
    with pytest.raises(TabelaRecusada) as e:
        pol.colunas_select("zz_pessoas")
    assert e.value.codigo == "coluna_proibida"
    assert P.filtra_colunas({"Codigo": 1, col: "ZZTESTE"}, ("Codigo", col)) == {"Codigo": 1}


def test_api_recusa_sem_texto_da_excecao(client, tokens):
    lista = client.get("/api/catalogo/sistemas", headers=tokens["admin"]).json()
    sid = next((x["id"] for x in lista if x["codigo"] == "IPTU"), None)
    if sid is None:
        r = client.post("/api/catalogo/sistemas", json={"codigo": "IPTU", "nome": "ZZTESTE IPTU"}, headers=tokens["admin"])
        assert r.status_code == 201, r.text
        sid = r.json()["id"]
    r = client.post(f"/api/catalogo/sistemas/{sid}/tabelas", json={"nome": "Rendas_bkp_2019", "chave": "id"},
                    headers=tokens["admin"])
    assert r.status_code == 422
    assert r.json()["detail"] == {"codigo": "tabela_padrao_recusado", "mensagem": "Tabela recusada pela política de dados."}
