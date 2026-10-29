"""QA #31 — adversarial. Só mock .invalid + ZZTESTE. Testes de reprodução do Tester (ciclo 1)."""
import logging
from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy import select

from app.connectors.sei import carga
from app.connectors.sei import projecao as pj
from app.connectors.sei.cliente import ClienteSei, OperacaoProibida, checa_operacao
from app.connectors.sei.config import ConfigSei
from app.models.camadas import RegistroBronze
from app.models.controle import CheckpointCarga, Execucao, Log
from tests import sei_mock as sm

NOITE = datetime(2026, 10, 29, 5, 0, tzinfo=UTC)
CPF = sm.CPF_OK
CPF_FMT = f"{CPF[:3]}.{CPF[3:6]}.{CPF[6:9]}-{CPF[9:]}"


@pytest.mark.parametrize("metodo,serv,cam", [
    ("GET", "processos", "/processos/%2E%2E/contatos"), ("GET", "processos", "/processos/..%2Fcontatos"),
    ("GET", "processos", "/processos/%252e%252e"), ("GET", "processos", "//evil.invalid/processos"),
    ("GET", "processos", "/processos/1.1%2F2026/%63ontatos"), ("HEAD", "processos", "/processos"),
    ("get ", "processos", "/processos"), ("GET", "processos", "/processos/http:%2F%2Fevil.invalid"),
    ("GET", "processos", "/processos/1.1%2F2026/documentos/x"), ("GET", "processos", "/processos;x=1"),
    ("GET", "processos", "/Processos"), ("GET", "processos", "/processos/" + CPF_FMT.replace("/", "%2F")),
    ("OPTIONS", "processos", "/processos"), ("GET", "processos", "processos"),
])
def test_qa_allowlist_adversarial(metodo, serv, cam):
    with pytest.raises(OperacaoProibida):
        checa_operacao(metodo, serv, cam)


def test_qa_protocolo_absoluto_nao_muda_host(monkeypatch):
    api = sm.Sei()
    sm.env_oficial(monkeypatch)
    with ClienteSei(ConfigSei.do_ambiente(), transport=api.transport(), dormir=lambda s: None) as c:
        for p in ("http://evil.invalid/x", "//evil.invalid", "../../contatos", "1.1/../../usuarios"):
            try:
                c.get("processo", p)
            except Exception:
                pass
    hosts = {r.url.host for r in api.chamadas}
    print("QA_HOSTS", hosts)
    assert hosts <= {"sei-processos.zzteste.invalid", "idp.zzteste.invalid"}


def test_qa_cpf_formatado_e_sigla_variantes():
    for i in ({"cpf": CPF_FMT}, {"sigla": CPF_FMT}, {"sigla": " " + CPF + " "}, {"cpf": int(CPF)}):
        assert pj.projeta_interessado({**i, "nome": "ZZTESTE"})["cidadao_id"]


def test_qa_cpf_no_nome_mascarado_na_visao():
    lst = [pj.projeta_interessado({"nome": f"ZZTESTE {CPF_FMT}"})]
    v = pj.visao_interessados(lst, "admin")
    print("QA_NOME_COM_CPF", v[0]["nome"])
    assert CPF not in str(v) and CPF_FMT not in str(v)


def test_qa_campos_extras_do_interessado_descartados():
    p = pj.projeta_interessado({"sigla": CPF, "nome": "ZZTESTE", "email": "z@aion.local", "x": {"cpf": CPF}})
    assert set(p) == {"cpf", "nome", "cidadao_id", "peso", "pendencia"}


def test_qa_documento_sem_nivel_nao_gravado():
    d = pj.projeta_documento({"idDocumento": "DX", "tipo": "G"}, "1", "oficial")
    print("QA_DOC_SEM_NIVEL", d)
    assert d is None


def test_qa_amostra_hml_libera_incremental_oficial(monkeypatch, db):
    for e in db.scalars(select(Execucao).where(Execucao.sistema == "SEI", Execucao.tipo == carga.TIPO_AMOSTRA)):
        e.status = "zzteste_isolado"  # isola de amostras oficiais de testes anteriores (banco _test)
    db.commit()
    sm.env_hml(monkeypatch)
    ex = carga.executar_amostra(db, "admin@aion.local", transport=sm.Sei().transport(), dormir=lambda s: None)
    assert ex.status == "sucesso" and ex.ambiente_origem == "homologacao"
    monkeypatch.delenv("SIGPMF_SRC_SEI_USAR_HOMOLOGACAO")
    monkeypatch.setenv("SIGPMF_SRC_SEI_CARGA_LIBERADA", "true")
    cfg = ConfigSei.do_ambiente()
    assert cfg.ambiente == "oficial"
    try:
        carga.valida_incremental(db, cfg, NOITE)
        liberou = True
    except carga.CargaRecusada:
        liberou = False
    print("QA_HML_LIBERA_OFICIAL", liberou)
    assert not liberou


def test_qa_andamento_de_processo_sigiloso_gravado(monkeypatch, db):
    a = pj.projeta_andamento({"idAndamento": "A1", "idProcesso": "9", "protocoloProcedimento": "1.1/2026",
                              "descricao": "ZZTESTE livre", "usuarios": {"nome": "ZZ"}}, "oficial",
                             pj.Publicos(ids=frozenset(), protocolos=frozenset()))
    print("QA_ANDAMENTO_SIGILOSO", a)
    assert a is None  # esperado: não gravar andamento de processo sigiloso


def test_qa_token_e_segredo_fora_de_log_em_erros(monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    def h(req):
        if req.method == "POST":
            return httpx.Response(200, json={"access_token": "ZZTOKSECRETO", "expires_in": 300})
        return httpx.Response(500, text="ZZTOKSECRETO " + sm.SEGREDO)
    sm.env_oficial(monkeypatch)
    with ClienteSei(ConfigSei.do_ambiente(), transport=httpx.MockTransport(h), dormir=lambda s: None) as c:
        with pytest.raises(Exception) as e:
            c.get("processos", None, {"start": 0, "limit": 1})
    txt = caplog.text + str(e.value) + repr(c)
    assert "ZZTOKSECRETO" not in txt and sm.SEGREDO not in txt and "zzteste.invalid" not in txt


def test_qa_hml_sem_flag_nao_usa_hml(monkeypatch):
    sm.env_hml(monkeypatch)
    monkeypatch.setenv("SIGPMF_SRC_SEI_USAR_HOMOLOGACAO", "false")
    for k in ("BASE_PROCESSOS", "BASE_PARAMETROS", "IDP_URL"):
        monkeypatch.delenv(f"SIGPMF_SRC_SEI_{k}")
    assert not ConfigSei.do_ambiente().habilitado


def test_qa_bronze_explorador_mascara_cpf_aninhado(monkeypatch, db):
    from app.explorador.mascara import mascara_dict
    r = db.scalars(select(RegistroBronze).where(RegistroBronze.sistema_origem == "SEI",
                                                RegistroBronze.tabela == "processos")).first()
    assert r is not None
    m = str(mascara_dict(dict(r.dados)))
    print("QA_BRONZE_CPF_CLARO", CPF in str(r.dados), "EXPLORADOR_CLARO", CPF in m)
    assert CPF not in m


# ------------------------------------------------------------------ correções do ciclo 1
@pytest.mark.parametrize("prot", ["123.456.789-09", CPF_FMT, f" {CPF} ", f"{CPF[:3]}-{CPF[3:]}"])
def test_b1_protocolo_que_vira_cpf_e_recusado(prot):
    from urllib.parse import quote
    with pytest.raises(OperacaoProibida):
        checa_operacao("GET", "processos", "/processos/" + quote(prot, safe=""))


def test_b1_cpf_recusado_antes_da_rede(monkeypatch):
    api = sm.Sei()
    sm.env_oficial(monkeypatch)
    with ClienteSei(ConfigSei.do_ambiente(), transport=api.transport(), dormir=lambda s: None) as c:
        with pytest.raises(OperacaoProibida):
            c.get("processo", CPF_FMT)
    assert not api.gets()


@pytest.mark.parametrize("nv", [None, "0", "1", "2", ""])
def test_b2_m3_sigilo_vem_do_processo_e_nao_do_andamento(nv):
    """M3: o campo do andamento (ausente no OpenAPI) não decide; vale o processo público na bronze."""
    a = {"idAndamento": "A1", "idProcesso": "9", "protocoloProcedimento": "1.1/2026"}
    if nv is not None:
        a["nivelAcesso"] = nv
    vazio = pj.Publicos(ids=frozenset(), protocolos=frozenset())
    assert pj.projeta_andamento(a, "oficial", vazio) is None
    pub = pj.Publicos(ids=frozenset({"9"}), protocolos=frozenset())
    assert pj.projeta_andamento(a, "oficial", pub)["id_andamento"] == "A1"
    pub_prot = pj.Publicos(ids=frozenset(), protocolos=frozenset({"1.1/2026"}))
    assert pj.projeta_andamento(a, "oficial", pub_prot)["id_andamento"] == "A1"


def test_m1_amostra_oficial_libera_incremental(monkeypatch, db):
    sm.env_oficial(monkeypatch)
    ex = carga.executar_amostra(db, "admin@aion.local", transport=sm.Sei().transport(), dormir=lambda s: None)
    assert ex.status == "sucesso" and ex.ambiente_origem == "oficial"
    monkeypatch.setenv("SIGPMF_SRC_SEI_CARGA_LIBERADA", "true")
    carga.valida_incremental(db, ConfigSei.do_ambiente(), NOITE)


@pytest.mark.parametrize("v,esperado", [("1", 1), ("2", 1), ("10", 1), ("10000", 1)])
def test_teto_paginas_travado_em_1(monkeypatch, caplog, v, esperado):
    sm.env_oficial(monkeypatch)
    monkeypatch.setenv("SIGPMF_SRC_SEI_MAX_PAGINAS_POR_EXECUCAO", v)
    caplog.set_level(logging.WARNING)
    assert ConfigSei.do_ambiente().max_paginas == esperado
    assert ("acima do teto" in caplog.text) == (v != "1")


def test_teto_paginas_padrao_1(monkeypatch):
    sm.env_oficial(monkeypatch)
    monkeypatch.delenv("SIGPMF_SRC_SEI_MAX_PAGINAS_POR_EXECUCAO", raising=False)
    assert ConfigSei.do_ambiente().max_paginas == 1


def test_0022_downgrade_sem_efeito():
    import importlib.util
    import pathlib
    p = pathlib.Path(__file__).parents[1] / "alembic" / "versions" / "0022_sei.py"
    spec = importlib.util.spec_from_file_location("m0022", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert "DROP" not in p.read_text().split("def downgrade")[1]
    assert mod.downgrade() is None


def test_bronze_sei_homologacao_marcada_sandbox(monkeypatch, db):
    from app.bronze.ingestao import ambiente_da_origem
    sm.env_hml(monkeypatch)
    assert ambiente_da_origem("SEI") == "sandbox"
    from app.bronze.ingestao import ingere
    ex = carga._nova_execucao(db, carga.TIPO_AMOSTRA, "admin@aion.local", "homologacao")
    ingere(db, "SEI", "zzteste_amb", "id", None, [{"id": "ZZTESTE-AMB-1"}], ex.id)
    r = db.scalar(select(RegistroBronze).where(RegistroBronze.sistema_origem == "SEI",
                                               RegistroBronze.tabela == "zzteste_amb"))
    assert r is not None and r.ambiente_origem == "sandbox"
    monkeypatch.delenv("SIGPMF_SRC_SEI_USAR_HOMOLOGACAO")
    sm.env_oficial(monkeypatch)
    assert ambiente_da_origem("SEI") == "producao"


# ------------------------------------------------------------------ produção por env (sem chamada real)
def test_token_path_configuravel_e_obrigatorio(monkeypatch):
    sm.env_oficial(monkeypatch)
    monkeypatch.setenv("SIGPMF_SRC_SEI_IDP_TOKEN_PATH", "/auth/realms/zz/protocol/openid-connect/token")
    cfg = ConfigSei.do_ambiente()
    assert cfg.habilitado
    assert cfg.idp_token_url == "https://idp.zzteste.invalid/auth/realms/zz/protocol/openid-connect/token"
    monkeypatch.delenv("SIGPMF_SRC_SEI_IDP_TOKEN_PATH")
    cfg = ConfigSei.do_ambiente()
    assert not cfg.habilitado and "SIGPMF_SRC_SEI_IDP_TOKEN_PATH" in cfg.pendencias()


@pytest.mark.parametrize("p", ["https://evil.invalid/token", "//evil.invalid/token", "/a/../token", "/a?x=1"])
def test_token_path_nao_muda_host(monkeypatch, p):
    sm.env_oficial(monkeypatch)
    monkeypatch.setenv("SIGPMF_SRC_SEI_IDP_TOKEN_PATH", p)
    assert not ConfigSei.do_ambiente().habilitado


def test_env_example_producao_sem_segredos():
    import pathlib
    raiz = pathlib.Path(__file__).parents[2]
    env = dict(linha.split("=", 1) for linha in (raiz / ".env.example").read_text().splitlines()
               if linha.startswith("SIGPMF_SRC_SEI_"))
    for k in ("BASE_PROCESSOS", "BASE_DOCUMENTOS", "BASE_PARAMETROS", "IDP_URL", "IDP_CLIENT_ID", "SIGLA_SISTEMA",
              "ID_UNIDADE", "REGIONAL"):
        assert env[f"SIGPMF_SRC_SEI_{k}"] == "", k  # B4: só nomes; valores oficiais em docs/INTEGRATIONS.md
    doc = (raiz / "docs" / "INTEGRATIONS.md").read_text()
    assert "https://sei-processos.api.rota.sp.gov.br" in doc and "110001339" in doc
    assert env["SIGPMF_SRC_SEI_IDP_CLIENT_SECRET"] == "" and env["SIGPMF_SRC_SEI_IDENTIFICACAO_SERVICO"] == ""
    assert env["SIGPMF_SRC_SEI_IDP_TOKEN_PATH"] == "" and env["SIGPMF_SRC_SEI_MAX_PAGINAS_POR_EXECUCAO"] == "1"
    comp = (raiz / "docker-compose.yml").read_text()
    assert "SIGPMF_SRC_SEI_IDP_CLIENT_SECRET: ${SIGPMF_SRC_SEI_IDP_CLIENT_SECRET:-}" in comp


# ------------------------------------------------------------------ M3 (ciclo 3): sigilo pelo processo na bronze
def _prep_m3(monkeypatch, db, **api_kw):
    sm.env_oficial(monkeypatch, CARGA_LIBERADA="true", MAX_PAGINAS_POR_EXECUCAO="5")
    from app.connectors.sei import config as cfg_sei
    monkeypatch.setattr(cfg_sei, "TETO_PAGINAS", 5)
    # banco de teste: tira da bronze o processo 2 gravado como público por testes anteriores;
    # na amostra abaixo ele vem restrito (não vai para a bronze). Processo 1 público.
    for r in db.scalars(select(RegistroBronze).where(RegistroBronze.sistema_origem == "SEI",
                                                     RegistroBronze.tabela == "processos")):
        if (r.dados or {}).get("protocolo") == "99990.000002/2026-01":
            db.delete(r)
    db.commit()
    am = carga.executar_amostra(db, "zz@aion.local", transport=sm.Sei(niveis={2: "1"}).transport(),
                                dormir=lambda s: None)
    assert am.status == "sucesso"
    monkeypatch.setattr(carga, "dentro_da_janela", lambda agora: (True, "00:00-06:00"))
    for ck in db.scalars(select(CheckpointCarga).where(CheckpointCarga.sistema == "SEI")):
        db.delete(ck)
    db.commit()
    return sm.Sei(**api_kw)


def _ck(db):
    return db.scalar(select(CheckpointCarga).where(CheckpointCarga.sistema == "SEI"))


def test_m3_payload_real_sem_nivel_grava_andamentos_de_processo_publico(monkeypatch, db):
    api = _prep_m3(monkeypatch, db, andamentos=7)
    corpo = api(httpx.Request("GET", "https://x.invalid/andamentos/completo?start=0&limit=50")).json()
    assert all("nivelAcesso" not in a for a in corpo["Andamentos"])  # mock alinhado ao OpenAPI
    ex = carga.executar_incremental(db, "zz@aion.local", NOITE, transport=api.transport(), dormir=lambda s: None)
    assert ex.status == "sucesso" and "andamentos_nao_gravados_sigilo=0" in ex.mensagem
    assert ex.novos + ex.atualizados + ex.ignorados == 7 and ex.lidos == 7
    assert _ck(db).ultima_chave == ["2026-10-29"]


@pytest.mark.parametrize("proc", [2, 77])  # 2 = restrito na amostra; 77 = desconhecido na bronze
def test_m3_processo_restrito_ou_desconhecido_descarta_e_nao_avanca(monkeypatch, db, proc):
    api = _prep_m3(monkeypatch, db, andamentos=6, processo_andamento=lambda i: 1 if i % 2 else proc)
    ex = carga.executar_incremental(db, "zz@aion.local", NOITE, transport=api.transport(), dormir=lambda s: None)
    assert ex.status == "parcial" and "andamentos_nao_gravados_sigilo=3" in ex.mensagem
    assert "checkpoint_avancou=False" in ex.mensagem and "andamentos_descartados" in ex.mensagem
    assert _ck(db) is None
    logs = [lg.mensagem for lg in db.scalars(select(Log).where(Log.execucao_id == ex.id, Log.nivel == "WARNING"))]
    assert logs and "andamentos_descartados=3" in logs[0]
    assert all(f"{proc:06d}" not in m and "ZZTESTE" not in m for m in logs)  # só contagem, sem identificação
    prots = {r.dados.get("protocolo") for r in db.scalars(select(RegistroBronze).where(
        RegistroBronze.sistema_origem == "SEI", RegistroBronze.tabela == "andamentos"))}
    assert f"99990.{proc:06d}/2026-01" not in prots
