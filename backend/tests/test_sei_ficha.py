"""Bloco Processos SEI na ficha (SF1–SF8). Só mock .invalid e ZZTESTE. CPF/CNPJ não entram na URL, no log nem no print."""
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import httpx
import pytest
from sqlalchemy import delete, select

from app.connectors.sei import carga
from app.connectors.sei.carta import rotulo_servico
from app.connectors.sei.cliente import ROTAS
from app.connectors.sei.concluido import status_processo
from app.connectors.sei.config import ConfigSei
from app.identity.correlacao import cidadao_id_por_cpf, empresa_id_por_cnpj
from app.identity.cpf import digitos_verificadores
from app.identity.documento import cnpj_valido
from app.models.camadas import Cidadao, RegistroBronze, SeiProcessoNaoPublico
from app.models.controle import Auditoria, SeiRevisaoInteressado
from app.models.prata_modelo import CidadaoEmpresa, Empresa
from app.services.sei_ficha import PESQUISA_PUBLICA, link_pesquisa_publica
from tests import sei_mock as sm

CPF = "900000001" + digitos_verificadores("900000001")
CPF_OUTRO = "900000002" + digitos_verificadores("900000002")
CPF_SOCIO = "900000003" + digitos_verificadores("900000003")
CPF_HOMONIMO = "900000004" + digitos_verificadores("900000004")
NOME_OUTRO = "ZZTESTE OUTRO INTERESSADO"
NOME_HOMONIMO = "ZZTESTE HOMONIMO SILVA"


def _cnpj(base12: str = "990000000001") -> str:
    def dv(parcial: str) -> str:
        pesos = list(range(len(parcial) - 7, 1, -1)) + list(range(9, 1, -1))
        r = sum((ord(c) - 48) * p for c, p in zip(parcial, pesos, strict=True)) % 11
        return "0" if r < 2 else str(11 - r)
    d1 = dv(base12)
    return base12 + d1 + dv(base12 + d1)


CNPJ = _cnpj()


def _apaga(db):
    db.rollback()
    db.execute(delete(RegistroBronze).where(RegistroBronze.id_origem.like("SF%")))
    db.execute(delete(SeiRevisaoInteressado).where(SeiRevisaoInteressado.id_procedimento.like("SF%")))
    db.execute(delete(SeiProcessoNaoPublico).where(SeiProcessoNaoPublico.id_processo.like("SF%")))
    db.execute(delete(CidadaoEmpresa).where(CidadaoEmpresa.chave_hash.like("sf%")))
    db.execute(delete(Empresa).where(Empresa.fonte_inscricao == "ZZTESTE", Empresa.inscricao_municipal.like("SF%")))
    for cid in (cidadao_id_por_cpf(CPF_SOCIO), cidadao_id_por_cpf(CPF_HOMONIMO)):
        db.execute(delete(Cidadao).where(Cidadao.cidadao_id == cid))
    db.commit()


@pytest.fixture
def isolado(db):
    _apaga(db)
    yield
    _apaga(db)


def _proc(i: int, *, tipo: str, nivel: str = "0", interessados=None, tarefa: str = "1",
          data_and: str = "03/10/2026 09:00", unidade: str = "ZZUN", descricao: str = "ZZTESTE andamento") -> dict:
    return {"idProcedimento": f"SF{i:06d}", "procedimentoFormatado": f"99990.{i:06d}/2026-01",
            "dataAutuacao": "02/09/2026", "nivelAcessoLocal": nivel, "nivelAcessoGlobal": nivel,
            "TipoProcedimento": [{"idTipoProcedimento": "1", "nome": tipo}],
            "interessados": interessados if interessados is not None else [{"sigla": CPF, "nome": "ZZTESTE CIDADAO"}],
            "ultimoAndamento": {"dataHora": data_and, "descricao": descricao, "idTarefa": tarefa,
                                "unidade": {"idUnidade": "1", "sigla": unidade}}}


class _Api:
    def __init__(self, processos: list[dict], docs: int = 2):
        self.processos = {p["procedimentoFormatado"]: p for p in processos}
        self.docs = docs
        self.gets: list[str] = []
        self.hosts: list[str] = []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.hosts.append(req.url.host)
        if req.method == "POST":
            return httpx.Response(200, json={"access_token": "tok-zz", "expires_in": 300})
        path = unquote(req.url.path)
        self.gets.append(path)
        if path == "/processos":
            return httpx.Response(200, json={"total": str(len(self.processos)), "processos": [
                {"idProcedimento": p["idProcedimento"], "protocoloProcedimento": prot}
                for prot, p in self.processos.items()]})
        if path.startswith("/processos/") and path.endswith("/documentos"):
            prot = path[len("/processos/"):-len("/documentos")]
            p = self.processos[prot]
            return httpx.Response(200, json={"total": str(self.docs), "documentos": [
                {"idDocumento": f"SFD{p['idProcedimento']}{k}", "tipo": "G", "idSerie": "7", "nivelAcesso": "0",
                 "idProcedimento": p["idProcedimento"], "procedimentoFormatado": prot,
                 "Unidade": {"idUnidade": "1", "sigla": "ZZ"}} for k in range(self.docs)]})
        if path.startswith("/processos/"):
            return httpx.Response(200, json=self.processos[path[len("/processos/"):]])
        return httpx.Response(404, json={})

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)


def _carga(monkeypatch, db, processos, **kw):
    sm.env_oficial(monkeypatch)
    api = _Api(processos, **kw)
    ex = carga.executar_amostra(db, "zz@aion.local", transport=api.transport(), dormir=lambda s: None)
    assert ex.status == "sucesso", ex.mensagem
    assert api.hosts and all(h.endswith(".invalid") for h in api.hosts)
    assert not any("soap" in c for c in api.gets)
    return api


def _libera(monkeypatch):
    monkeypatch.setenv("SIGPMF_SRC_SEI_CARGA_LIBERADA", "true")


def _get(client, tokens, caminho):
    return client.get(caminho, headers=tokens["leitura"])


# ------------------------------------------------------------------ regra e de-para
def test_regra_de_concluido_isolada_e_de_para_da_carta(monkeypatch):
    monkeypatch.delenv("SIGPMF_SEI_TAREFAS_CONCLUIDO", raising=False)
    monkeypatch.delenv("SIGPMF_SEI_TEXTO_CONCLUIDO", raising=False)
    assert status_processo("32", "ZZTESTE processo arquivado") == "Em andamento"
    monkeypatch.setenv("SIGPMF_SEI_TAREFAS_CONCLUIDO", "32, 48")
    monkeypatch.setenv("SIGPMF_SEI_TEXTO_CONCLUIDO", "concluido|processo encerrado")
    assert status_processo("32", None) == "Concluído"
    assert status_processo("1", "ZZTESTE Processo Encerrado na unidade") == "Concluído"
    assert status_processo("1", "ZZTESTE ainda tramita") == "Em andamento"
    assert rotulo_servico("Autorização para corte de árvore") == "Corte de árvore"
    assert rotulo_servico("Certidão de área construída (habite-se)") == "Certidão de área construída"
    assert rotulo_servico("ZZTESTE tipo livre") == "ZZTESTE tipo livre"


def test_sf5_link_so_com_protocolo():
    link = link_pesquisa_publica("99990.000101/2026-01")
    u = urlparse(link)
    assert u.scheme == "https" and u.netloc == "cidades.sei.sp.gov.br" and u.path.startswith("/franca")
    assert link.startswith(PESQUISA_PUBLICA)
    qs = parse_qs(u.query)
    assert set(qs) == {"acao_externa", "txtProtocoloPesquisa"}
    assert qs["txtProtocoloPesquisa"] == ["99990.000101/2026-01"]
    for proibido in (CPF, CNPJ, "ZZTESTE", "nome", "cpf", "cnpj"):
        assert proibido not in link


# ------------------------------------------------------------------ SF1
def test_sf1_cidadao_lista_tres_processos_com_todos_os_campos(monkeypatch, client, tokens, db, isolado):
    monkeypatch.setenv("SIGPMF_SEI_TAREFAS_CONCLUIDO", "77")
    procs = [
        _proc(101, tipo="Autorização para corte de árvore", data_and="03/10/2026 09:00", unidade="ZZARB",
              descricao="ZZTESTE andamento do corte"),
        _proc(102, tipo="ZZTESTE tipo livre", data_and="01/10/2026 08:00", unidade="ZZSEC",
              descricao="ZZTESTE andamento antigo"),
        _proc(103, tipo="Certidão de área construída (habite-se)", tarefa="77", data_and="05/10/2026 15:00",
              unidade="ZZOBR", descricao="ZZTESTE andamento da certidao"),
    ]
    _carga(monkeypatch, db, procs)
    _libera(monkeypatch)
    cid = cidadao_id_por_cpf(CPF)
    r = _get(client, tokens, f"/api/gold/cidadaos/{cid}/sei")
    assert r.status_code == 200, r.text
    assert CPF not in r.text and CNPJ not in r.text
    corpo = r.json()
    assert corpo["fonte_liberada"] is True and corpo["total"] == 3 and corpo["mensagem"] is None
    assert [p["protocolo"] for p in corpo["processos"]] == [
        "99990.000101/2026-01", "99990.000102/2026-01", "99990.000103/2026-01"]
    assert [p["status"] for p in corpo["processos"]] == ["Em andamento", "Em andamento", "Concluído"]
    corte, livre, cert = corpo["processos"]
    assert corte["rotulo"] == "Corte de árvore" and corte["servico_carta"] == "Corte de árvore"
    assert corte["tipo_sei"] == "Autorização para corte de árvore"
    assert livre["rotulo"] == "ZZTESTE tipo livre" and livre["servico_carta"] is None
    assert cert["rotulo"] == "Certidão de área construída"
    for p in corpo["processos"]:
        assert p["data_autuacao"] == "2026-09-02" and p["unidade_atual"]
        assert p["ultimo_andamento"]["data_hora"] and p["ultimo_andamento"]["descricao"]
        assert p["documentos"] == 2
        assert "cidades.sei.sp.gov.br/franca" in p["link"] and p["protocolo"].split("/")[0] in p["link"]
        assert CPF not in p["link"] and "ZZTESTE" not in p["link"]
    aud = db.scalars(select(Auditoria).where(Auditoria.acao == "consulta_sei_ficha",
                                              Auditoria.alvo == f"cidadao:{cid}").order_by(Auditoria.id.desc())).first()
    assert aud.usuario == "leitura@aion.local" and aud.detalhe["total"] == 3
    assert CPF not in str(aud.detalhe)


# ------------------------------------------------------------------ SF2
def test_sf2_cnpj_na_ficha_da_empresa_e_nao_na_do_socio(monkeypatch, client, tokens, db, isolado):
    assert cnpj_valido(CNPJ)
    socio = cidadao_id_por_cpf(CPF_SOCIO)
    empresa = empresa_id_por_cnpj(CNPJ)
    db.add(Cidadao(cidadao_id=socio, nome="ZZTESTE SOCIO"))
    emp = Empresa(cnpj=CNPJ, fonte_inscricao="ZZTESTE", inscricao_municipal="SF2", razao_social="ZZTESTE EMPRESA LTDA")
    db.add(emp)
    db.flush()
    db.add(CidadaoEmpresa(cidadao_id=socio, empresa_id=emp.id, papel="socio", sistema="ZZTESTE",
                          tabela="sf", chave_hash="sf2socio"))
    db.commit()
    prot = "99990.000201/2026-01"
    _carga(monkeypatch, db, [_proc(201, tipo="ZZTESTE alvara", interessados=[
        {"cnpj": CNPJ, "nome": "ZZTESTE EMPRESA LTDA"}])])
    _libera(monkeypatch)
    emp_r = _get(client, tokens, f"/api/gold/empresas/{empresa}/sei")
    assert emp_r.status_code == 200 and emp_r.json()["total"] == 1
    assert emp_r.json()["processos"][0]["protocolo"] == prot
    assert CNPJ not in emp_r.text and CPF_SOCIO not in emp_r.text
    socio_r = _get(client, tokens, f"/api/gold/cidadaos/{socio}/sei")
    assert socio_r.status_code == 200 and socio_r.json()["total"] == 0
    assert prot not in socio_r.text and CNPJ not in socio_r.text
    assert db.scalar(select(CidadaoEmpresa).where(CidadaoEmpresa.chave_hash == "sf2socio")) is not None


# ------------------------------------------------------------------ SF3
@pytest.mark.parametrize("nivel", ["1", "2"])
def test_sf3_restrito_ou_sigiloso_nao_aparece_nem_na_contagem(monkeypatch, client, tokens, db, isolado, nivel):
    i = 310 if nivel == "1" else 320
    publico = _proc(i, tipo="ZZTESTE publico")
    oculto = _proc(i + 1, tipo="ZZTESTE oculto", nivel=nivel, descricao="ZZTESTE nao pode vazar")
    _carga(monkeypatch, db, [publico, oculto])
    _libera(monkeypatch)
    cid = cidadao_id_por_cpf(CPF)
    r = _get(client, tokens, f"/api/gold/cidadaos/{cid}/sei")
    assert r.status_code == 200
    assert r.json()["total"] == 1
    assert oculto["procedimentoFormatado"] not in r.text
    assert "ZZTESTE nao pode vazar" not in r.text
    assert db.scalar(select(RegistroBronze).where(RegistroBronze.id_origem == oculto["idProcedimento"])) is None
    marcador = db.scalar(select(SeiProcessoNaoPublico).where(SeiProcessoNaoPublico.id_processo == oculto["idProcedimento"]))
    assert marcador is not None and marcador.nivel_acesso == nivel
    assert db.scalar(select(SeiRevisaoInteressado).where(
        SeiRevisaoInteressado.id_procedimento == oculto["idProcedimento"])) is None


# ------------------------------------------------------------------ SF4
def test_sf4_so_nome_nao_liga_homonimo_e_fica_na_fila(monkeypatch, client, tokens, db, isolado):
    hom = cidadao_id_por_cpf(CPF_HOMONIMO)
    db.add(Cidadao(cidadao_id=hom, nome=NOME_HOMONIMO))
    db.commit()
    proc = _proc(401, tipo="ZZTESTE requerimento", interessados=[{"nome": NOME_HOMONIMO}])
    _carga(monkeypatch, db, [proc])
    _libera(monkeypatch)
    r = _get(client, tokens, f"/api/gold/cidadaos/{hom}/sei")
    assert r.status_code == 200 and r.json()["total"] == 0 and r.json()["mensagem"] == "Nenhum processo SEI"
    assert proc["procedimentoFormatado"] not in r.text
    fila = db.scalar(select(SeiRevisaoInteressado).where(SeiRevisaoInteressado.id_procedimento == "SF000401"))
    assert fila is not None and fila.pendencia == "revisao_nome" and fila.nome == NOME_HOMONIMO
    bronze = db.scalar(select(RegistroBronze).where(RegistroBronze.id_origem == "SF000401"))
    interessado = bronze.dados["interessados"][0]
    assert interessado["cidadao_id"] is None and interessado["cnpj"] is None and interessado["peso"] is None
    assert interessado["pendencia"] == "revisao_nome"


# ------------------------------------------------------------------ SF6
def test_sf6_sem_processo_ou_fonte_travada(monkeypatch, client, tokens, db, isolado):
    monkeypatch.delenv("SIGPMF_SRC_SEI_CARGA_LIBERADA", raising=False)
    cid = cidadao_id_por_cpf(CPF)
    r = _get(client, tokens, f"/api/gold/cidadaos/{cid}/sei")
    assert r.status_code == 200
    assert r.json()["mensagem"] == "Fonte SEI ainda não liberada" and r.json()["processos"] == [] and r.json()["total"] == 0
    _carga(monkeypatch, db, [_proc(601, tipo="ZZTESTE tipo")])
    monkeypatch.delenv("SIGPMF_SRC_SEI_CARGA_LIBERADA", raising=False)
    travado = _get(client, tokens, f"/api/gold/cidadaos/{cid}/sei")
    assert travado.status_code == 200 and travado.json()["mensagem"] == "Fonte SEI ainda não liberada"
    assert travado.json()["total"] == 0 and "99990.000601" not in travado.text
    _libera(monkeypatch)
    vazio = _get(client, tokens, f"/api/gold/cidadaos/{cidadao_id_por_cpf(CPF_OUTRO)}/sei")
    assert vazio.status_code == 200 and vazio.json()["fonte_liberada"] is True
    assert vazio.json()["mensagem"] == "Nenhum processo SEI" and vazio.json()["processos"] == []


def test_sf6_carga_continua_travada_por_padrao(monkeypatch, db):
    sm.env_oficial(monkeypatch)
    monkeypatch.delenv("SIGPMF_SRC_SEI_CARGA_LIBERADA", raising=False)
    cfg = ConfigSei.do_ambiente()
    assert cfg.carga_liberada is False
    with pytest.raises(carga.CargaRecusada) as exc:
        carga.valida_incremental(db, cfg, datetime.now(UTC))
    assert "CARGA_LIBERADA" in str(exc.value)


# ------------------------------------------------------------------ SF7
def test_sf7_outros_mascarados_e_revelar_com_motivo(monkeypatch, client, tokens, db, isolado):
    _carga(monkeypatch, db, [_proc(701, tipo="ZZTESTE tipo", interessados=[
        {"sigla": CPF, "nome": "ZZTESTE CIDADAO"},
        {"sigla": CPF_OUTRO, "nome": NOME_OUTRO},
    ])])
    _libera(monkeypatch)
    cid = cidadao_id_por_cpf(CPF)
    lista = _get(client, tokens, f"/api/gold/cidadaos/{cid}/sei")
    assert lista.status_code == 200 and lista.json()["processos"][0]["tem_outros"] is True
    assert CPF_OUTRO not in lista.text and NOME_OUTRO not in lista.text
    corpo = {"id_procedimento": "SF000701"}
    for perfil in ("admin", "operador", "leitura"):
        r = client.post(f"/api/gold/cidadaos/{cid}/sei/interessados", headers=tokens[perfil], json=corpo)
        assert r.status_code == 200, r.text
        assert CPF_OUTRO not in r.text and NOME_OUTRO not in r.text
        item = r.json()["interessados"][0]
        assert item["nome"] and "*" in item["nome"] and item["cpf"] and item["cpf"] != CPF_OUTRO
        assert CPF not in r.text  # o próprio interessado da ficha não entra na lista dos outros
    assert client.post(f"/api/gold/cidadaos/{cid}/sei/interessados/revelar", headers=tokens["admin"],
                       json={**corpo, "motivo": "ZZTESTE motivo longo"}).status_code == 403
    assert client.post(f"/api/gold/cidadaos/{cid}/sei/interessados/revelar", headers=tokens["admin_lgpd"],
                       json=corpo).status_code == 400
    rev = client.post(f"/api/gold/cidadaos/{cid}/sei/interessados/revelar", headers=tokens["admin_lgpd"],
                      json={**corpo, "motivo": "ZZTESTE conferir interessado"})
    assert rev.status_code == 200 and CPF_OUTRO in rev.text and NOME_OUTRO in rev.text
    aud = db.scalars(select(Auditoria).where(Auditoria.acao == "revelar_sei_ficha_interessados").order_by(Auditoria.id.desc())).first()
    assert aud.usuario == "admin_lgpd@aion.local" and aud.alvo == f"cidadao:{cid}"
    assert aud.detalhe["motivo"].startswith("ZZTESTE") and CPF_OUTRO not in str(aud.detalhe) and NOME_OUTRO not in str(aud.detalhe)


# ------------------------------------------------------------------ SF8
def test_sf8_migration_allowlist_e_auditoria_da_ficha():
    txt = Path(__file__).resolve().parents[1].joinpath("alembic/versions/0052_sei_revisao_interessado.py").read_text()
    upgrade = txt.split("def downgrade")[0].upper()
    assert "IF NOT EXISTS" in upgrade
    for proibido in ("DROP ", "DELETE ", "TRUNCATE"):
        assert proibido not in upgrade
    assert set(ROTAS) == {"processos", "processo", "documentos_processo", "andamentos", "tipos",
                          "unidades", "assuntos", "series"}
    assert {r.servico for r in ROTAS.values()} == {"processos", "parametros"}


def test_sf8_abrir_bloco_grava_quem_viu_qual_ficha(monkeypatch, client, tokens, db, isolado):
    _libera(monkeypatch)
    cid = cidadao_id_por_cpf(CPF_HOMONIMO)
    r = _get(client, tokens, f"/api/gold/cidadaos/{cid}/sei")
    assert r.status_code == 200 and CPF_HOMONIMO not in r.request.url.path
    aud = db.scalars(select(Auditoria).where(Auditoria.acao == "consulta_sei_ficha",
                                              Auditoria.alvo == f"cidadao:{cid}").order_by(Auditoria.id.desc())).first()
    assert aud is not None and aud.usuario == "leitura@aion.local"
    assert CPF_HOMONIMO not in str(aud.detalhe) and CPF_HOMONIMO not in (aud.alvo or "")
