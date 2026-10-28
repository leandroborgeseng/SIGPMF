"""Conector da API SIL: empresas por CCM (keyset de 50 em 50), token renovado, falhas repetidas, só leitura.

A API simulada reproduz o comportamento conferido em produção (28/10/2026): Sequencia=N devolve as próximas 50
empresas com CCM >= N, campos vazios como {} ou "", e acaba devolvendo lista vazia.
"""
import httpx
import pytest
from sqlalchemy import func, select

from app.connectors.sil.conector import ConfigSil, ErroSil, SilConnector, achata_empresa
from app.connectors.sqlserver.politicas import politica
from app.core import recursos
from app.core.config import get_settings
from app.models.camadas import RegistroBronze
from app.models.controle import Execucao
from app.services import carga_completa as ccomp
from app.services import carga_conector as cc

SECRET = "segredo-sintetico-sil"


class ApiSil:
    def __init__(self, ccms, falhas_500=0, expirar_na=None):
        self.ccms = sorted(ccms)
        self.falhas_500, self.expirar_na = falhas_500, expirar_na
        self.tokens, self.chamadas, self.metodos = 0, 0, set()

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.metodos.add((req.method, req.url.path))
        if req.url.path.endswith("/api/authenticate"):
            assert req.method == "POST" and SECRET in req.content.decode()
            self.tokens += 1
            return httpx.Response(200, json={"access_token": f"tok{self.tokens}", "token_type": "Bearer",
                                             "expires_in": "600s"})
        assert req.method == "GET"  # só leitura
        if self.falhas_500:
            self.falhas_500 -= 1
            return httpx.Response(502, text="bad gateway")
        self.chamadas += 1
        if self.expirar_na == self.chamadas and req.headers["Authorization"] == "Bearer tok1":
            return httpx.Response(401, json={"message": "token expirado"})
        seq = int(req.url.params["Sequencia"])
        lote = [c for c in self.ccms if c >= seq][:50]
        return httpx.Response(200, json={"empresas": [{"empresa": {
            "Ccm": str(c), "Nome": f"EMPRESA SINTETICA {c}", "CNPJCPF": {} if c % 7 == 0 else f"00.000.{c:03d}/0001-00",
            "PossuiDebito": "S" if c % 2 else "N", "Email": f"contato{c}@exemplo.invalid", "Telefone": "0000",
            "AreaUtilizada": {}, "AreaOcupada": "", "LocalEntrega": {"EntregaEndereco": "X"},
            "LocalEmpresa": {"Endereco": f"RUA SINTETICA, {c}", "Bairro": "CENTRO", "CEP": "14400-000", "Complemento": ""},
        }} for c in lote]})


def _sil(api, max_lotes=100, **kw):
    """Leitor unificado sobre o ClienteSil, com transporte mock (nenhuma rede)."""
    cfg = ConfigSil("https://sil.invalid/v1", "cliente", SECRET, "pmfranca", req_por_s=20.0, **kw)
    return SilConnector(cfg, transporte=httpx.MockTransport(api), esperar=lambda s: None, amostra_limite=120,
                        max_lotes=max_lotes)


def test_achata_sem_email_telefone_e_campos_vazios_viram_nulo():
    r = achata_empresa({"Ccm": "42", "CNPJCPF": {}, "Nome": " X ", "Email": "a@b", "Telefone": "1", "AreaOcupada": "",
                        "LocalEmpresa": {"Bairro": "B", "Complemento": ""}})
    assert r["Ccm"] == 42 and r["CNPJCPF"] is None and r["Nome"] == "X" and r["AreaOcupada"] is None
    assert r["Complemento"] is None and "Email" not in r and "Telefone" not in r
    assert set(r) == set(politica("SIL").colunas_select("Empresas"))


def test_keyset_de_50_em_50_ate_acabar_com_buracos_no_ccm():
    ccms = [*range(1, 131), *range(500, 520), 99999999]           # 151 empresas, com buraco e registro de teste
    api = ApiSil(ccms)
    con = _sil(api)
    s = con.abrir()
    try:
        p1 = con.ler_pagina(s, "Empresas", None, ("Ccm",), None, 100)
        assert [r["Ccm"] for r in p1] == list(range(1, 101))
        p2 = con.ler_pagina(s, "Empresas", None, ("Ccm",), (p1[-1]["Ccm"],), 100)
        assert [r["Ccm"] for r in p2] == [*range(101, 131), *range(500, 520), 99999999]
        assert con.ler_pagina(s, "Empresas", None, ("Ccm",), (99999999,), 100) == []
    finally:
        s.close()
    assert api.tokens == 1 and all(m == "GET" for m, p in api.metodos if not p.endswith("authenticate"))


def test_token_expirado_renova_e_repete_e_502_tenta_de_novo():
    api = ApiSil(range(1, 60), falhas_500=2, expirar_na=2)
    con = _sil(api)
    rows = con.ler_amostra("Empresas")
    assert [r["Ccm"] for r in rows] == list(range(1, 60))
    assert api.tokens == 2                                       # renovou depois do 401


def test_erros_sem_segredo_na_mensagem():
    def recusa(req):
        return httpx.Response(401, json={"error": "invalid_client"})
    con = SilConnector(ConfigSil("https://sil.invalid/v1", "cliente", SECRET, "x"),
                       transporte=httpx.MockTransport(recusa), esperar=lambda s: None, amostra_limite=10)
    r = con.testar_conexao()
    assert r["ok"] is False and SECRET not in r["erro"] and "cliente" not in r["erro"]
    assert SECRET not in repr(con.config) and "cliente" not in repr(con.config)
    with pytest.raises(ErroSil):
        SilConnector(ConfigSil(None, None, None, None), amostra_limite=10).abrir()


def test_sil_registrado_como_conector_de_fato():
    assert cc.CONECTORES["sil"][0] == "SIL" and "sil" in ccomp.CONECTORES_COMPLETA
    assert cc.tabelas_do_conector("SIL") == ["Empresas"]
    from app.silver.processamento import sistemas_so_fatos
    assert "SIL" in sistemas_so_fatos()                          # nunca cria nem correlaciona cidadão


@pytest.fixture()
def api_sil(monkeypatch):
    api = ApiSil([*range(1, 121), 777])
    con = _sil(api)
    monkeypatch.setenv("CARGA_COMPLETA_LOTE", "50")
    monkeypatch.setenv("CARGA_COMPLETA_PAUSA_MS", "0")
    monkeypatch.setattr(recursos, "disco", lambda: recursos.Uso(total=100, livre=50))
    get_settings.cache_clear()
    monkeypatch.setattr(cc, "leitor_de", lambda conector, cfg: con)

    def sincrono(ex_id, conector, trava, somente_camadas=False):
        ccomp.rodar(ex_id, conector, trava, leitor=con, somente_camadas=somente_camadas)
    monkeypatch.setattr(ccomp, "disparar", sincrono)
    yield api
    get_settings.cache_clear()


def test_amostra_e_carga_completa_da_sil_ate_a_bronze(db, api_sil):
    a = cc.executar_amostra(db, "admin@aion.local", "sil")
    assert a.status in ("sucesso", "sem_novidades"), a.mensagem
    ex = ccomp.iniciar(db, "admin@aion.local", "sil")
    db.expire_all()
    ex = db.get(Execucao, ex.id)
    assert ex.status == "sucesso", ex.mensagem
    assert "sem_contagem" not in (ex.mensagem or "") or "reconciliacao=ok" in ex.mensagem
    n = db.scalar(select(func.count(func.distinct(RegistroBronze.id_origem))).where(
        RegistroBronze.sistema_origem == "SIL", RegistroBronze.tabela == "Empresas", RegistroBronze.quarentena.is_(None)))
    assert n == 121
    rec = ccomp.reconciliacao(db, "SIL")["Empresas"]
    assert rec["status"] == "sem_contagem" and rec["ok"] and rec["bronze"] == 121
    dados = db.scalar(select(RegistroBronze.dados).where(RegistroBronze.sistema_origem == "SIL").limit(1))
    assert "Email" not in dados and "Telefone" not in dados


# ---------------------------------------------------------------- prata: empresas ligadas ao cidadão pelo CPF

def _cpf(base9: str) -> str:
    from app.identity.cpf import digitos_verificadores
    return base9 + digitos_verificadores(base9)


@pytest.fixture()
def empresas_na_bronze(db):
    from sqlalchemy import delete

    from app.models.camadas import Cidadao, Identificador
    from app.silver import tributario
    cpf = _cpf("731450286")
    cid = "sil-teste-cidadao-0001"
    db.add(Cidadao(cidadao_id=cid, nome="Titular Sintetico SIL"))
    db.add(Identificador(cidadao_id=cid, tipo="cpf", valor=cpf))
    ex = Execucao(tipo="teste_sil", sistema="SIL", status="sucesso", usuario="teste")
    db.add(ex)
    db.flush()
    linhas = [(900001, f"{cpf[:3]}.{cpf[3:6]}.{cpf[6:9]}-{cpf[9:]}", "MEI SINTETICO", "S"),
              (900002, "12.345.678/0001-95", "LTDA SINTETICA", "N"),
              (900003, None, "SEM DOCUMENTO", None)]
    for ccm, doc, nome, deb in linhas:
        db.add(RegistroBronze(sistema_origem="SIL", tabela="Empresas", id_origem=str(ccm), hash=f"h{ccm}", execucao_id=ex.id,
                              dados={"Ccm": ccm, "CNPJCPF": doc, "Nome": nome, "PossuiDebito": deb, "Bairro": "CENTRO"}))
    db.commit()
    res = tributario.reconstruir(db)
    db.commit()
    yield cid, res
    db.execute(delete(RegistroBronze).where(RegistroBronze.execucao_id == ex.id))
    db.execute(delete(Identificador).where(Identificador.cidadao_id == cid))
    db.execute(delete(Cidadao).where(Cidadao.cidadao_id == cid))
    db.commit()
    tributario.reconstruir(db)
    db.commit()


def test_prata_liga_empresa_ao_cidadao_so_pelo_cpf(db, empresas_na_bronze):
    from app.models.camadas import EmpresaSil
    cid, res = empresas_na_bronze
    mei, ltda, sem = (db.get(EmpresaSil, c) for c in (900001, 900002, 900003))
    assert mei.cidadao_id == cid and mei.tipo_pessoa == "PF" and mei.possui_debito is True
    assert ltda.cidadao_id is None and ltda.tipo_pessoa == "PJ" and ltda.documento == "12345678000195"
    assert sem.documento is None and sem.cidadao_id is None and sem.possui_debito is None
    assert res["empresas"] >= 3 and res["empresas_com_cidadao"] >= 1


def test_ficha_mostra_empresas_do_cpf_mascaradas(client, tokens, empresas_na_bronze):
    cid, _ = empresas_na_bronze
    r = client.get(f"/api/gold/cidadaos/{cid}/tributario", headers=tokens["admin"])
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["totais"]["empresas"] == 1
    e = d["empresas"][0]
    assert e["ccm"] == 900001 and e["nome"] == "MEI SINTETICO" and e["possui_debito"] is True
    assert "731450" not in e["documento"]                          # documento sempre mascarado


def test_vinculos_pessoa_empresa_e_volta(db, empresas_na_bronze):
    from app.services.vinculos import explorar
    cid, _ = empresas_na_bronze
    pf = explorar(db, f"pf:{cid}")
    emp = [v for v in pf["vizinhos"] if v["grupo"] == "Empresas"]
    assert [v["id"] for v in emp] == ["em:900001"] and emp[0]["tipo"] == "EM"
    assert any(f["texto"] == "Débito na SIL" for f in emp[0]["flags"])
    em = explorar(db, "em:900001")
    assert em["no"]["nome"] == "MEI SINTETICO" and "731450" not in em["no"]["doc"]
    assert [v["id"] for v in em["vizinhos"] if v["grupo"] == "Titular"] == [f"pf:{cid}"]
