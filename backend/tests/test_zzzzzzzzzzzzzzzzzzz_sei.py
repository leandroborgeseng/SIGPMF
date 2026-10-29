"""Conector SEI (aceite SEI1-SEI8). Só SEI simulado (httpx.MockTransport, hosts .invalid) e dados ZZTESTE."""
import logging
import pathlib
import re
from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy import select

from app.connectors.sei import carga
from app.connectors.sei import projecao as pj
from app.connectors.sei.cliente import ROTAS, ClienteSei, FalhaCredencial, OperacaoProibida, SeiDesabilitado, checa_operacao
from app.connectors.sei.config import ConfigSei
from app.connectors.sei.paginacao import paginar
from app.models.camadas import RegistroBronze
from app.models.controle import Auditoria, CheckpointCarga, Execucao
from tests import sei_mock as sm

NOITE = datetime(2026, 10, 29, 5, 0, tzinfo=UTC)  # 02:00 em São Paulo


def _cli(api, monkeypatch, **cfg):
    sm.env_oficial(monkeypatch, **cfg)
    return ClienteSei(ConfigSei.do_ambiente(), transport=api.transport(), dormir=lambda s: None)


# ------------------------------------------------------------------ SEI1 allowlist
@pytest.mark.parametrize("servico,caminho", [
    ("processos", "/processos"), ("processos", "/processos/99990.000001%2F2026-01"),
    ("processos", "/processos/99990.000001%2F2026-01/documentos"), ("processos", "/andamentos/completo"),
    ("processos", "/processos/tipos"), ("parametros", "/unidades"), ("parametros", "/assuntos"),
    ("parametros", "/series")])
def test_sei1_allowlist_aceita_so_os_gets_da_v1(servico, caminho):
    assert checa_operacao("GET", servico, caminho)


@pytest.mark.parametrize("metodo,servico,caminho", [
    ("POST", "processos", "/processos"), ("PUT", "processos", "/processos/1.1%2F2026/nivelacesso"),
    ("DELETE", "processos", "/processos/99990.000001%2F2026-01"), ("POST", "documentos", "/documentos"),
    ("POST", "documentos", "/emails"), ("POST", "documentos", "/arquivos"), ("GET", "parametros", "/contatos"),
    ("GET", "parametros", "/usuarios"), ("GET", "processos", "/processos/12345678909/atribuicao"),
    ("GET", "processos", "/processos/12345678909"), ("GET", "documentos", "/documentos/0001/conteudo"),
    ("GET", "documentos", "/documentos/0001/anexos"), ("GET", "documentos", "/documentos/0001"),
    ("GET", "processos", "/processos/andamentos"), ("GET", "processos", "/processos/individual"),
    ("GET", "processos", "/processos/marcadores"), ("GET", "parametros", "/blocos/1"),
    ("GET", "parametros", "/processos"), ("GET", "processos", "/unidades"), ("GET", "processos", "/../contatos"),
    ("GET", "processos", "/processos?x=1"), ("GET", "parametros", "/CONTATOS"),
    ("GET", "processos", "/processos/1.1%2F2026/andamento"), ("POST", "parametros", "/replica-usuario")])
def test_sei1_recusa_escrita_contatos_usuarios_conteudo_anexos(metodo, servico, caminho):
    with pytest.raises(OperacaoProibida):
        checa_operacao(metodo, servico, caminho)


def test_sei1_recusa_antes_da_rede(monkeypatch):
    api = sm.Sei()
    with _cli(api, monkeypatch) as cli:
        for rota in ("contatos", "usuarios", "conteudo", "anexos", "atribuicao"):
            with pytest.raises(OperacaoProibida):
                cli.get(rota)
        with pytest.raises(OperacaoProibida):  # cpf como protocolo (colisão /processos/{cpf}/atribuicao)
            cli.get("processo", sm.CPF_OK)
        with pytest.raises(OperacaoProibida):  # parâmetro fora da allowlist (dado pessoal na query)
            cli.get("processos", None, {"cpf": sm.CPF_OK})
    assert api.chamadas == []


def test_sei1_documentos_service_sem_rotas_na_v1():
    assert {r.servico for r in ROTAS.values()} == {"processos", "parametros"}


def test_sei1_cinco_cabecalhos_e_bearer(monkeypatch):
    api = sm.Sei()
    with _cli(api, monkeypatch) as cli:
        cli.get("processos", None, {"start": 0, "limit": 5})
        cli.get("unidades", None, {"start": 0, "limit": 5})
    g1, g2 = api.gets()
    for h in ("xTraceIdSP", "xSiglaSistema", "xIdentificacaoServico", "xIdUnidade", "xRegional"):
        assert g1.headers.get(h)
    assert g1.headers["Authorization"] == "Bearer tok-1"
    assert g1.headers["xIdentificacaoServico"] == sm.SERVICO
    assert "xIdUnidade" not in g2.headers and g2.headers.get("xRegional")
    assert g1.headers["xTraceIdSP"] != g2.headers["xTraceIdSP"]


# ------------------------------------------------------------------ SEI2 token
def test_sei2_401_renova_uma_vez(monkeypatch):
    api = sm.Sei(token_401=1)
    with _cli(api, monkeypatch) as cli:
        assert cli.get("processos", None, {"start": 0, "limit": 1})["processos"]
    assert api.tokens == 2 and len(api.gets()) == 2


def test_sei2_segundo_401_vira_falha_credencial(monkeypatch):
    def h(req):
        if req.method == "POST":
            return httpx.Response(200, json={"access_token": "tok-x", "expires_in": 300})
        return httpx.Response(401)
    sm.env_oficial(monkeypatch)
    with ClienteSei(ConfigSei.do_ambiente(), transport=httpx.MockTransport(h)) as cli:
        with pytest.raises(FalhaCredencial):
            cli.get("processos", None, {"start": 0, "limit": 1})


def test_sei2_segredo_e_token_nunca_em_log(monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    api = sm.Sei(token_401=1)
    with _cli(api, monkeypatch) as cli:
        cli.get("processo", "99990.000001/2026-01", {"sinRetornarInteressados": "S"})
        texto = caplog.text + repr(cli) + repr(cli.config)
    for proibido in (sm.SEGREDO, sm.SERVICO, "tok-1", "tok-2", "zzteste.invalid", "99990.000001", "zzteste-client"):
        assert proibido not in texto


# ------------------------------------------------------------------ SEI3 amostra
def test_sei3_amostra_1_pagina_ate_5_detalhes(monkeypatch, db):
    sm.env_oficial(monkeypatch)
    api = sm.Sei(n_processos=80)
    ex = carga.executar_amostra(db, "zz@aion.local", transport=api.transport(), dormir=lambda s: None)
    assert ex.status == "sucesso" and ex.ambiente_origem == "oficial"
    listas = [c for c in api.gets() if c.url.path == "/processos"]
    assert len(listas) == 1 and int(listas[0].url.params["limit"]) <= 50
    detalhes = [c for c in api.gets() if c.url.path.startswith("/processos/") and
                not c.url.path.endswith("/documentos")]
    assert len(detalhes) == 5
    assert ex.lidos == 50
    aud = db.scalars(select(Auditoria).where(Auditoria.acao == "sei_amostra").order_by(Auditoria.id.desc())).first()
    assert aud.detalhe["processos_lidos"] == 50 and aud.detalhe["ambiente_origem"] == "oficial"
    tudo = str(aud.detalhe) + (ex.mensagem or "")
    assert "ZZTESTE" not in tudo and sm.CPF_OK not in tudo  # só contagens


def test_sei3_incremental_bloqueado_sem_liberacao(monkeypatch, db):
    sm.env_oficial(monkeypatch)
    with pytest.raises(carga.CargaRecusada, match="validação do Leandro"):
        carga.executar_incremental(db, "zz@aion.local", NOITE)


def test_sei3_incremental_fora_da_janela(monkeypatch, db):
    sm.env_oficial(monkeypatch, CARGA_LIBERADA="true")
    carga.executar_amostra(db, "zz@aion.local", transport=sm.Sei().transport())
    monkeypatch.setattr(carga, "dentro_da_janela", lambda agora: (False, "00:00-06:00"))
    with pytest.raises(carga.CargaRecusada, match="janela"):
        carga.executar_incremental(db, "zz@aion.local", NOITE)


def test_sei3_rotas_amostra_e_status(monkeypatch, client, tokens):
    sm.env_oficial(monkeypatch, BASE_PROCESSOS=None)
    st = client.get("/api/sei/status", headers=tokens["admin"]).json()
    assert st["habilitado"] is False and "SIGPMF_SRC_SEI_BASE_PROCESSOS" in st["pendencias"]
    assert sm.SEGREDO not in str(st) and "zzteste.invalid" not in str(st)
    assert client.get("/api/sei/status", headers=tokens["operador"]).status_code == 403
    assert client.post("/api/sei/amostra", headers=tokens["operador"]).status_code == 403


# ------------------------------------------------------------------ SEI4 paginação
def test_sei4_start_avanca_pelos_itens_e_para_na_pagina_curta(monkeypatch):
    api = sm.Sei(andamentos=120)
    with _cli(api, monkeypatch) as cli:
        r = paginar(cli, "andamentos", "Andamentos", lambda a: a.get("idAndamento"), 50, 10,
                    params={"dataInicio": "01/10/2026", "dataFim": "02/10/2026"})
    assert [int(c.url.params["start"]) for c in api.gets()] == [0, 50, 100]
    assert len(r["itens"]) == 120 and r["fim"] == "pagina_curta" and not r["parcial"]


def test_sei4_para_na_pagina_vazia(monkeypatch):
    api = sm.Sei(andamentos=100)
    with _cli(api, monkeypatch) as cli:
        r = paginar(cli, "andamentos", "Andamentos", lambda a: a.get("idAndamento"), 50, 10)
    assert r["fim"] == "pagina_vazia" and len(r["itens"]) == 100 and r["paginas"] == 3 and not r["parcial"]


def test_sei4_pagina_repetida_para_parcial(monkeypatch):
    api = sm.Sei(andamentos=200, repetir_andamentos=True)
    with _cli(api, monkeypatch) as cli:
        r = paginar(cli, "andamentos", "Andamentos", lambda a: a.get("idAndamento"), 50, 10)
    assert r["fim"] == "pagina_repetida" and r["parcial"] and len(r["itens"]) == 50 and r["paginas"] == 2


def _prepara_incremental(monkeypatch, db, **api_kw):
    sm.env_oficial(monkeypatch, CARGA_LIBERADA="true", MAX_PAGINAS_POR_EXECUCAO="5")
    from app.connectors.sei import config as cfg_sei
    monkeypatch.setattr(cfg_sei, "TETO_PAGINAS", 5)  # mecânica da paginação; o teto real (1) tem teste próprio
    assert carga.executar_amostra(db, "zz@aion.local", transport=sm.Sei().transport()).status == "sucesso"
    monkeypatch.setattr(carga, "dentro_da_janela", lambda agora: (True, "00:00-06:00"))
    for ck in db.scalars(select(CheckpointCarga).where(CheckpointCarga.sistema == "SEI")):
        db.delete(ck)
    db.commit()
    return sm.Sei(**api_kw)


def test_sei4_incremental_avanca_checkpoint_por_datafim(monkeypatch, db):
    api = _prepara_incremental(monkeypatch, db, andamentos=70)
    ex = carga.executar_incremental(db, "zz@aion.local", NOITE, transport=api.transport())
    assert ex.status == "sucesso" and ex.lidos == 70 and ex.ambiente_origem == "oficial"
    p = api.gets()[0].url.params
    assert p["dataInicio"] == "28/10/2026" and p["dataFim"] == "29/10/2026"
    ck = db.scalar(select(CheckpointCarga).where(CheckpointCarga.sistema == "SEI"))
    assert ck.ultima_chave == ["2026-10-29"]
    linha = db.scalar(select(RegistroBronze).where(RegistroBronze.sistema_origem == "SEI",
                                                   RegistroBronze.tabela == "andamentos").limit(1))
    assert linha.hash and linha.dados["ambiente_origem"] == "oficial"
    assert "descricao" not in linha.dados and "usuarios" not in linha.dados


def test_sei4_pagina_repetida_nao_avanca_checkpoint(monkeypatch, db):
    api = _prepara_incremental(monkeypatch, db, andamentos=200, repetir_andamentos=True)
    ex = carga.executar_incremental(db, "zz@aion.local", NOITE, transport=api.transport())
    assert ex.status == "parcial" and "pagina_repetida" in ex.mensagem
    assert db.scalar(select(CheckpointCarga).where(CheckpointCarga.sistema == "SEI")) is None


def test_sei4_teto_de_paginas_e_parcial(monkeypatch, db):
    api = _prepara_incremental(monkeypatch, db, andamentos=1000)
    monkeypatch.setenv("SIGPMF_SRC_SEI_MAX_PAGINAS_POR_EXECUCAO", "1")
    ex = carga.executar_incremental(db, "zz@aion.local", NOITE, transport=api.transport())
    assert ex.status == "parcial" and len(api.gets()) == 1
    ck = db.scalar(select(CheckpointCarga).where(CheckpointCarga.sistema == "SEI"))
    # M4: janela não terminou; guarda (dataInicio, dataFim, próximo start) e não fecha o dia
    assert ck.ultima_chave == ["2026-10-28", "2026-10-29", 50] and ck.concluida is False


# ------------------------------------------------------------------ SEI5 interessados
def test_sei5_cpf_liga_ao_cidadao_e_sem_cpf_vai_para_revisao():
    a = pj.projeta_interessado({"sigla": sm.CPF_OK, "nome": "ZZTESTE A"})
    b = pj.projeta_interessado({"sigla": "zzorg", "nome": "ZZTESTE B"})
    c = pj.projeta_interessado({"cpf": "111.111.111-11", "nome": "ZZTESTE C"})
    assert a["cidadao_id"] and a["peso"] == 1.0 and a["pendencia"] is None
    assert b["cidadao_id"] is None and b["pendencia"] == "revisao_nome"
    assert c["cidadao_id"] is None and c["pendencia"] == "revisao_nome"


def test_sei5_mascarado_fora_do_admin_lgpd_e_revelar_com_motivo(monkeypatch, db, client, tokens):
    sm.env_oficial(monkeypatch)
    carga.executar_amostra(db, "zz@aion.local", transport=sm.Sei(n_processos=1).transport())
    corpo = {"id_procedimento": "9000001"}
    for perfil in ("admin", "operador", "admin_lgpd"):
        r = client.post("/api/sei/interessados", headers=tokens[perfil], json=corpo)
        assert r.status_code == 200
        assert sm.CPF_OK not in r.text and "ZZTESTE FULANO DA SILVA" not in r.text
    assert client.post("/api/sei/interessados", headers=tokens["leitura"], json=corpo).status_code == 403
    assert client.post("/api/sei/interessados/revelar", headers=tokens["admin"],
                       json={**corpo, "motivo": "ZZTESTE auditoria"}).status_code == 403
    assert client.post("/api/sei/interessados/revelar", headers=tokens["admin_lgpd"], json=corpo).status_code == 400
    r = client.post("/api/sei/interessados/revelar", headers=tokens["admin_lgpd"],
                    json={**corpo, "motivo": "ZZTESTE verificação de cadastro"})
    assert r.status_code == 200 and sm.CPF_OK in r.text
    aud = db.scalars(select(Auditoria).where(Auditoria.acao == "revelar_sei_interessados")
                     .order_by(Auditoria.id.desc())).first()
    assert aud.detalhe["motivo"].startswith("ZZTESTE") and sm.CPF_OK not in str(aud.detalhe)


# ------------------------------------------------------------------ SEI6 sigilo e conteúdo
def test_sei6_sigilosos_e_restritos_nao_sao_gravados(monkeypatch, db):
    sm.env_oficial(monkeypatch)
    db.query(RegistroBronze).filter(RegistroBronze.sistema_origem == "SEI").delete()
    db.commit()
    api = sm.Sei(n_processos=5, niveis={2: "1", 3: "2"})
    ex = carga.executar_amostra(db, "zz@aion.local", transport=api.transport())
    ids = set(db.scalars(select(RegistroBronze.id_origem).where(RegistroBronze.sistema_origem == "SEI",
                                                                 RegistroBronze.tabela == "processos")))
    assert ids == {"9000001", "9000004", "9000005"}
    assert "nao_gravados_sigilo=2" in ex.mensagem
    docs_sig = [c for c in api.gets() if c.url.path.endswith("/documentos") and ".000002/" in c.url.path]
    assert docs_sig == []  # nem os documentos do restrito são lidos


def test_sei6_documento_so_metadados_e_sem_conteudo(monkeypatch, db):
    sm.env_oficial(monkeypatch)
    api = sm.Sei(n_processos=1)
    carga.executar_amostra(db, "zz@aion.local", transport=api.transport())
    docs = list(db.scalars(select(RegistroBronze).where(RegistroBronze.sistema_origem == "SEI",
                                                        RegistroBronze.tabela == "documentos")))
    assert docs and "DSIG" not in {d.id_origem for d in docs}
    for d in docs:
        assert set(d.dados) <= {"id_documento", "tipo", "id_serie", "id_procedimento", "protocolo_processo",
                                "unidade", "nivel_acesso", "ambiente_origem"}
    assert not any(c.url.path.endswith(("/conteudo", "/anexos")) for c in api.chamadas)
    proc = db.scalar(select(RegistroBronze).where(RegistroBronze.sistema_origem == "SEI",
                                                  RegistroBronze.tabela == "processos").limit(1))
    assert "observacoes" not in proc.dados and "linkAcesso" not in proc.dados


# ------------------------------------------------------------------ SEI7 URL por variável, sem fallback
def test_sei7_sem_url_inerte_409(monkeypatch, client, tokens):
    sm.env_oficial(monkeypatch, BASE_PROCESSOS=None, IDP_URL=None)
    assert ConfigSei.do_ambiente().habilitado is False
    with pytest.raises(SeiDesabilitado):
        ClienteSei(ConfigSei.do_ambiente())
    r = client.post("/api/sei/amostra", headers=tokens["admin"])
    assert r.status_code == 409 and sm.SEGREDO not in r.text
    assert client.post("/api/sei/incremental", headers=tokens["admin"]).status_code == 409


def test_sei7_homologacao_so_com_flag_e_sem_fallback(monkeypatch):
    for k in sm.HML:
        monkeypatch.delenv(f"SIGPMF_SRC_SEI_HML_{'IDP_URL' if k == 'IDP' else 'BASE_' + k}", raising=False)
    monkeypatch.delenv("SIGPMF_SRC_SEI_USAR_HOMOLOGACAO", raising=False)
    sm.env_oficial(monkeypatch, BASE_PROCESSOS=None)
    for k, v in sm.HML.items():  # hml configurada, flag desligada: não é usada
        monkeypatch.setenv(f"SIGPMF_SRC_SEI_HML_{'IDP_URL' if k == 'IDP' else 'BASE_' + k}", v)
    c = ConfigSei.do_ambiente()
    assert c.ambiente == "oficial" and not c.habilitado
    monkeypatch.setenv("SIGPMF_SRC_SEI_HML_IDP_TOKEN_PATH", "/token")
    monkeypatch.setenv("SIGPMF_SRC_SEI_USAR_HOMOLOGACAO", "true")
    c = ConfigSei.do_ambiente()
    assert c.ambiente == "homologacao" and c.habilitado and c.bases["processos"] == sm.HML["PROCESSOS"]
    monkeypatch.delenv("SIGPMF_SRC_SEI_HML_BASE_PROCESSOS")  # flag ligada sem hml: não cai na oficial
    sm.env_oficial(monkeypatch)
    c = ConfigSei.do_ambiente()
    assert c.ambiente == "homologacao" and not c.habilitado


def test_sei7_url_hml_como_oficial_fica_inerte(monkeypatch):
    sm.env_oficial(monkeypatch, BASE_PROCESSOS=sm.HML["PROCESSOS"])
    c = ConfigSei.do_ambiente()
    assert not c.habilitado and any("homologação" in p for p in c.pendencias())


def test_sei7_ambiente_registrado_e_chamadas_so_no_ambiente_escolhido(monkeypatch, db, caplog):
    caplog.set_level(logging.INFO, logger="sigpmf.conector.sei")
    sm.env_hml(monkeypatch)
    api = sm.Sei(n_processos=1)
    ex = carga.executar_amostra(db, "zz@aion.local", transport=api.transport())
    assert ex.ambiente_origem == "homologacao" and "ambiente_origem=homologacao" in ex.mensagem
    assert all(".api-hml." in str(c.url) or "idp-hml" in str(c.url) for c in api.chamadas)
    assert "SEI[homologacao]" in caplog.text and "zzteste.invalid" not in caplog.text
    linha = db.scalars(select(RegistroBronze).where(RegistroBronze.sistema_origem == "SEI",
                                                    RegistroBronze.execucao_id == ex.id)).first()
    assert linha.dados["ambiente_origem"] == "homologacao"


def test_sei7_sem_host_fixo_no_codigo():
    pasta = pathlib.Path(__file__).resolve().parents[1] / "app" / "connectors" / "sei"
    for f in pasta.glob("*.py"):
        txt = f.read_text()
        assert not re.search(r"https?://", txt), f.name
        assert "sp.gov.br" not in txt and "api-hml" not in txt, f.name


# ------------------------------------------------------------------ SEI8 mock, ZZTESTE e migração aditiva
def test_sei8_migracao_0022_so_aditiva():
    txt = (pathlib.Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0022_sei.py").read_text()
    up = txt.split("def upgrade", 1)[1].split("def downgrade", 1)[0]
    assert "ADD COLUMN" in up
    for proibido in ("DROP", "DELETE", "TRUNCATE", "ALTER COLUMN", "RENAME", "UPDATE"):
        assert proibido not in up.upper()


def test_sei8_coluna_ambiente_origem_existe(db):
    ex = Execucao(tipo="zz", sistema="ZZTESTE", status="sucesso", ambiente_origem="oficial")
    db.add(ex)
    db.commit()
    assert db.get(Execucao, ex.id).ambiente_origem == "oficial"


def test_sei8_testes_sem_rede_real_e_sem_material_da_origem():
    raiz = pathlib.Path(__file__).resolve().parents[1]
    txt = (raiz / "tests" / "sei_mock.py").read_text()
    assert "sp.gov.br" not in txt
    hosts = re.findall(r"https://([A-Za-z0-9.-]+)", txt)
    assert hosts and all(h.endswith(".invalid") for h in hosts)
