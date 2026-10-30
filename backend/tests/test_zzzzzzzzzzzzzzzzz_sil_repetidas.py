"""Investigação 28/10: 1º lote da SIL em produção deu lidos=49, novos=1, ignorados=48.
Só a SIL simulada (httpx.MockTransport, hosts .invalid); nenhuma rede real."""
import httpx
import pytest
from sqlalchemy import func, select

from app.bronze.ingestao import ingere
from app.connectors.sil.conector import achata_empresa
from app.models.camadas import RegistroBronze
from app.models.controle import Execucao
from app.services import carga_conector as cc
from tests.test_zzzzzzzzzzzzzzzz_sil_unificado import Api, _cfg, _checkpoint_sil, _con, _zera_checkpoint_sil, sil_mock  # noqa: F401  (fixture)


class ApiCompleta(Api):
    """Empresas com todos os campos (LocalEmpresa aninhado, {} em campos vazios), como a SIL."""

    def __call__(self, req):
        r = super().__call__(req)
        if req.url.path.endswith("consultarlote"):
            seq = int(req.url.params["Sequencia"])
            lote = [c for c in self.ccms if c >= seq][:50]
            return httpx.Response(200, json={"data": {"empresas": [{"empresa": {
                "Ccm": f" {c} ", "CNPJCPF": f"ZZ{c:012d}", "Nome": f"ZZTESTE {c}", "PossuiDebito": "N",
                "LocalEmpresa": {"Endereco": "RUA ZZ", "Bairro": {}, "CEP": "", "Complemento": {}},
                "AreaOcupada": {}, "AreaUtilizada": "10"}} for c in lote]}})
        return r


def _ex(db):
    ex = Execucao(tipo="amostra_conector", sistema="SIL", status="em_andamento", usuario="zz@aion.local")
    db.add(ex)
    db.flush()
    return ex.id


def _sem_bronze_sil(db):
    db.query(RegistroBronze).filter(RegistroBronze.sistema_origem == "SIL").delete()
    db.commit()


def test_a_ccm_string_vira_int_e_keyset_avanca():
    api = Api(ccms=range(1, 151))
    con = _con(api, max_lotes=3)
    s = con.abrir()
    s.teto = con.max_lotes  # SL1: teto só na sessão da amostra
    try:
        linhas = con.ler_pagina(s, "Empresas", None, ("Ccm",), None, 1000)
    finally:
        s.close()
    assert api.sequencias == [1, 51, 101]
    assert [r["Ccm"] for r in linhas] == list(range(1, 151))
    assert all(isinstance(r["Ccm"], int) for r in linhas)


def test_a_ordenacao_numerica_nao_lexica():
    api = Api(ccms=[9, 10, 99, 100, 1000])
    con = _con(api)
    s = con.abrir()
    try:
        assert [r["Ccm"] for r in con.lote_empresas(s, 1)] == [9, 10, 99, 100, 1000]
    finally:
        s.close()


def test_b_achata_mantem_ccm_distinto():
    ids = {achata_empresa({"Ccm": str(c), "LocalEmpresa": {}})["Ccm"] for c in range(1, 51)}
    assert len(ids) == 50


def test_b_ingere_50_distintas_da_50_novas(db):
    _sem_bronze_sil(db)
    linhas = [achata_empresa({"Ccm": str(c), "Nome": f"ZZ {c}"}) for c in range(1, 51)]
    c = ingere(db, "SIL", "Empresas", "Ccm", None, linhas, execucao_id=_ex(db), confirmar=False)
    assert c["novos"] == 50 and c["ignorados"] == 0
    db.rollback()


def test_c_api_repetindo_conta_ignorados_mas_keyset_avanca(db):
    _sem_bronze_sil(db)
    linhas = [achata_empresa({"Ccm": "7", "Nome": "ZZ"})] * 49
    c = ingere(db, "SIL", "Empresas", "Ccm", None, linhas, execucao_id=_ex(db), confirmar=False)
    assert (c["lidos"], c["novos"], c["ignorados"]) == (49, 1, 48)  # assinatura idêntica à de produção
    db.rollback()


@pytest.mark.usefixtures("sil_mock")
def test_amostra_ponta_a_ponta_50_distintas_50_novas(db, monkeypatch):
    _sem_bronze_sil(db)
    _zera_checkpoint_sil(db)
    api = ApiCompleta(ccms=range(1, 2001))
    monkeypatch.setattr(cc, "leitor_de", lambda conector, cfg: _con(api))
    a = cc.executar_amostra(db, "admin@aion.local", "sil")
    assert (a.lidos, a.novos, a.ignorados) == (50, 50, 0), a.mensagem
    n = db.scalar(select(func.count(func.distinct(RegistroBronze.id_origem)))
                  .where(RegistroBronze.sistema_origem == "SIL"))
    assert n == 50


# ---------------------------------------------------------------- guarda: página repetida / sem CCM novo

class ApiPaginas(Api):
    """Devolve, por Sequencia, a lista de CCMs configurada (com repetições, se houver); senão o padrão."""

    def __init__(self, paginas, **kw):
        super().__init__(**kw)
        self.paginas = paginas

    def __call__(self, req):
        if req.url.path.endswith("consultarlote"):
            seq = int(req.url.params["Sequencia"])
            if seq in self.paginas:
                self.hits.append((req.url.host, req.method, req.url.path))
                self.sequencias.append(seq)
                return httpx.Response(200, json={"empresas": [{"empresa": {"Ccm": str(c), "Nome": f"ZZ {c}"}}
                                                              for c in self.paginas[seq]]})
        return super().__call__(req)


def _ler(api, max_lotes=20, ultima=None):
    con = _con(api, max_lotes=max_lotes)
    s = con.abrir()
    try:
        return con.ler_pagina(s, "Empresas", None, ("Ccm",), ultima, 5000), s.parcial(), api.sequencias
    finally:
        s.close()


def test_pagina_inteira_repetida_para_com_alerta_sem_pular():
    linhas, parcial, seqs = _ler(ApiPaginas({1: [7] * 49}, ccms=range(1, 3001)))
    assert [r["Ccm"] for r in linhas] == [7]                      # 1 linha por CCM
    assert seqs == [1]                                            # não gasta o teto em duplicatas
    assert parcial["alerta"] == {"motivo": "ccm_repetido", "sequencia": 1, "itens": 49, "ccms_distintos": 1,
                                 "repetidas": 48, "abaixo_da_sequencia": 0}


def test_pagina_parcialmente_repetida_grava_distintos_e_para():
    pag = list(range(51, 91)) + [60] * 10
    linhas, parcial, seqs = _ler(ApiPaginas({51: pag}, ccms=range(1, 3001)), ultima=(50,))
    assert [r["Ccm"] for r in linhas] == list(range(51, 91)) and seqs == [51]
    assert parcial["alerta"]["motivo"] == "ccm_repetido" and parcial["alerta"]["repetidas"] == 10


def test_pagina_sem_ccm_novo_nao_avanca():
    linhas, parcial, seqs = _ler(ApiPaginas({101: [3, 4, 5]}, ccms=range(1, 3001)), ultima=(100,))
    assert linhas == [] and seqs == [101]
    assert parcial["alerta"]["motivo"] == "sem_ccm_novo" and parcial["alerta"]["abaixo_da_sequencia"] == 3


def test_paginas_normais_sem_alerta_com_teto_alto():
    linhas, parcial, seqs = _ler(Api(ccms=range(1, 1001)))
    assert seqs[:3] == [1, 51, 101] and len(linhas) == len({r["Ccm"] for r in linhas}) == 1000
    assert "alerta" not in (parcial or {})


@pytest.mark.usefixtures("sil_mock")
def test_amostra_com_pagina_repetida_fica_parcial_com_alerta(db, monkeypatch):
    _sem_bronze_sil(db)
    api = ApiPaginas({1: [7] * 49}, ccms=range(1, 3001))
    monkeypatch.setattr(cc, "leitor_de", lambda conector, cfg: _con(api))
    a = cc.executar_amostra(db, "admin@aion.local", "sil")
    assert (a.lidos, a.novos, a.ignorados) == (1, 1, 0), a.mensagem
    assert "ALERTA ccm_repetido: 1 CCM distinto(s) em 49 itens" in a.mensagem


@pytest.mark.usefixtures("sil_mock")
def test_carga_completa_com_pagina_repetida_para_e_checkpoint_no_ccm_gravado(db, monkeypatch):
    _sem_bronze_sil(db)
    api = Api(ccms=range(1, 3001))
    monkeypatch.setattr(cc, "leitor_de", lambda conector, cfg: _con(api, max_lotes=20))
    cc.executar_amostra(db, "admin@aion.local", "sil")
    _zera_checkpoint_sil(db)
    api2 = ApiPaginas({101: [120] * 50}, ccms=range(1, 3001))
    monkeypatch.setattr(cc, "leitor_de", lambda conector, cfg: _con(api2, max_lotes=20))
    from app.models.controle import Execucao as Ex
    from app.services import carga_completa as ccomp
    ex = ccomp.iniciar(db, "admin@aion.local", "sil")
    db.expire_all()
    ex = db.get(Ex, ex.id)
    assert ex.status == "parcial" and "página anômala da API (ccm_repetido" in ex.mensagem, ex.mensagem
    assert api2.sequencias == [1, 51, 101]                        # parou na página repetida, sem gastar o teto
    cp = _checkpoint_sil(db)
    assert cp.concluida is False and cp.ultima_chave == [120]     # só até o CCM distinto de fato gravado
    _zera_checkpoint_sil(db)
