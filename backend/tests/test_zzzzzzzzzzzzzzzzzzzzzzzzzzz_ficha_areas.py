"""Ficha do cidadão com todas as áreas: fontes carregadas, cemitério e totais de saúde e impostos."""
from datetime import date, timedelta

from sqlalchemy import delete

from app.catalogo.bases import BASES
from app.models.camadas import Atendimento, Cidadao, Identificador, Lancamento, RegistroBronze, Vacinacao, Vinculo
from app.models.controle import Auditoria, Execucao
from app.services import ficha_cidadao

CID = "ficha-areas-1"
VAZIO = "ficha-areas-2"


def _prepara(db):
    for m in (Vinculo, Atendimento, Vacinacao, Lancamento):
        db.execute(delete(m).where(m.cidadao_id.in_([CID, VAZIO])))
    db.execute(delete(Identificador).where(Identificador.cidadao_id == CID))
    for c in (CID, VAZIO):
        if db.get(Cidadao, c) is None:
            db.add(Cidadao(cidadao_id=c, nome="Pessoa Ficticia", data_nascimento=date(1970, 1, 1)))
    db.flush()
    db.execute(delete(RegistroBronze).where(RegistroBronze.sistema_origem == "CEMITERIO", RegistroBronze.id_origem.like("ficha-%")))
    ex = Execucao(tipo="teste", sistema="CEMITERIO", status="sucesso", modo="completa")
    db.add(ex)
    db.flush()
    linhas = [
        ("Cemiterio", {"codcemite": 7, "nomecemite": "Municipal Ficticio"}),
        ("Titulares", {"Cod_Cemiterio": "7", "Quadra": "Q1", "Sepultura": "S1", "Sequencia": 1, "Titular": "OUTRO NOME", "CPF": "12345678909", "Endereco": "RUA SECRETA", "Ativo": "S", "Inclusao": "C"}),
        ("Titulares", {"Cod_Cemiterio": "7", "Quadra": "Q1", "Sepultura": "S2", "Sequencia": 1, "Titular": "OUTRO NOME", "CPF": "12345678909", "Ativo": "N", "Inclusao": "C"}),
        ("Pessoas_Autorizadas", {"Cod_Cemiterio": "9", "Quadra": "Q3", "Sepultura": "S3", "Sequencia": 1, "Nome": "X", "CPF": "12345678909", "Parentesco": "Filho", "Inclusao": "x"}),
        ("Leilao", {"Cod_Cemiterio": "7", "Quadra": "Q9", "Sepultura": "S9", "DataVenda": "2024-02-02", "DataLiberacao": None, "Nome": "X", "CPF": "12345678909"}),
        ("Obitos", {"COD_CEMITERIO": "7", "QUADRA": "Q2", "SEPULTURA": "S4", "GAVETA": "1", "LADO": "A", "FALECIDO": "X", "DT_MORTE": "2020-01-01", "DT_SEPULTADO": "2020-01-02"}),
    ]
    for i, (tab, d) in enumerate(linhas):
        r = RegistroBronze(sistema_origem="CEMITERIO", tabela=tab, id_origem=f"ficha-{i}", dados=d, hash=f"h{i}", execucao_id=ex.id)
        db.add(r)
        db.flush()
        if tab != "Cemiterio":
            db.add(Vinculo(cidadao_id=CID, sistema="CEMITERIO", tabela=tab, chave_origem=f"ficha-{i}", bronze_id=r.id, metodo="cpf", confianca=1.0, atributos=[]))
    db.add(Identificador(cidadao_id=CID, tipo="cns", valor="898001234567890"))
    db.commit()


def test_cemiterio_sem_dado_de_terceiro_e_auditado(client, tokens, db):
    _prepara(db)
    r = client.get(f"/api/gold/cidadaos/{CID}/cemiterio", headers=tokens["leitura"])
    assert r.status_code == 200
    d = r.json()
    assert [(t["sepultura"], t["ativo"]) for t in d["titular"]] == [("S1", True), ("S2", False)]
    assert d["titular"][0]["cemiterio"] == "Municipal Ficticio"
    assert d["autorizado"] == [{"cemiterio": "Cemitério 9", "quadra": "Q3", "sepultura": "S3", "parentesco": "Filho"}]
    assert d["leilao"][0]["data_venda"] == "2024-02-02"
    assert d["sepultamento"][0]["data_sepultamento"] == "2020-01-02"
    for proibido in ("OUTRO NOME", "RUA SECRETA", "12345678909"):
        assert proibido not in r.text
    ult = db.query(Auditoria).filter(Auditoria.acao == "consulta_cemiterio").order_by(Auditoria.id.desc()).first()
    assert ult.alvo == f"cidadao:{CID}"
    v = client.get(f"/api/gold/cidadaos/{VAZIO}/cemiterio", headers=tokens["leitura"]).json()
    assert v["titular"] == v["autorizado"] == v["leilao"] == v["sepultamento"] == []
    assert client.get("/api/gold/cidadaos/nao-existe/cemiterio", headers=tokens["leitura"]).status_code == 404


def test_fontes_sem_pessoa_e_nao_integradas_fora_do_implementado(client, tokens, db):
    _prepara(db)
    r = client.get("/api/gold/ficha/fontes", headers=tokens["leitura"])
    assert r.status_code == 200
    assert r.json()["carregadas"]["cemiterio"] is True
    assert set(r.json()["carregadas"]) >= {"vacinas", "consultas", "agendamentos", "imoveis", "lancamentos", "dividas", "empresas", "educacao"}
    assert client.get("/api/gold/ficha/fontes").status_code == 401
    status = {b[0]: b[3] for b in BASES}
    for _area, base in ficha_cidadao.NAO_INTEGRADAS:  # base que passar a ser lida sai da lista
        assert status.get(base) not in (None, "implementado"), base


def test_totais_contam_tudo_mesmo_com_lista_limitada(client, tokens, db):
    _prepara(db)
    ontem = date.today() - timedelta(days=1)
    db.add_all(Lancamento(chave_origem=f"ficha-l{i}", cidadao_id=CID, exercicio=2020, tributo="001", parcela=i, codi="1", vencimento=ontem - timedelta(days=i),
                          valor=10, pago=False) for i in range(205))
    db.add_all(Atendimento(chave_origem=f"ficha-a{i}", cidadao_id=CID, data=ontem) for i in range(205))
    db.add(Vacinacao(chave_origem="ficha-v", cidadao_id=CID, data=ontem, vacina="Influenza"))
    db.commit()
    t = client.get(f"/api/gold/cidadaos/{CID}/tributario", headers=tokens["leitura"]).json()
    assert len(t["lancamentos"]) == 200
    assert t["totais"]["lancamentos"] == 205 and t["totais"]["parcelas_vencidas"] == 205 and t["totais"]["em_aberto"] == 2050
    s = client.get(f"/api/gold/cidadaos/{CID}/saude", headers=tokens["leitura"]).json()
    assert len(s["atendimentos"]) == 200 and s["totais"] == {"atendimentos": 205, "vacinas": 1}


def test_ficha_traz_cns_mascarado(client, tokens, db):
    from app.models.camadas import CidadaoSistemas
    _prepara(db)
    if db.get(CidadaoSistemas, CID) is None:
        db.add(CidadaoSistemas(cidadao_id=CID, nome="Pessoa Ficticia", sistemas=[], dominios=[]))
        db.commit()
    d = client.get(f"/api/gold/cidadaos/{CID}", headers=tokens["leitura"]).json()
    assert d["cns"] == "*** 0012 3456 ****" and "898001234567890" not in str(d) and d["ve_cadastro"] is True
