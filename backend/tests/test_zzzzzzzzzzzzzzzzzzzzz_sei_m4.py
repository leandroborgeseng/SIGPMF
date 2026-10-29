"""SEI M4 / B5 / B6 / reclassificação (PR de sequência do #31). Só dados sintéticos ZZTESTE, hosts .invalid."""
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from app.connectors.sei import carga
from app.connectors.sei import projecao as pj
from app.models.camadas import RegistroBronze, SeiProcessoNaoPublico
from app.models.controle import Auditoria, CheckpointCarga, Log, SeiPaiPendente
from tests import sei_mock as sm


def _noite(dia: int) -> datetime:
    return datetime(2026, 10, dia, 5, 0, tzinfo=UTC)


def _prep(monkeypatch, db, paginas="5", amostra=None):
    sm.env_oficial(monkeypatch, CARGA_LIBERADA="true", MAX_PAGINAS_POR_EXECUCAO=paginas)
    from app.connectors.sei import config as cfg_sei
    monkeypatch.setattr(cfg_sei, "TETO_PAGINAS", int(paginas))
    am = carga.executar_amostra(db, "zz@aion.local", transport=(amostra or sm.Sei()).transport(),
                                dormir=lambda s: None)
    assert am.status == "sucesso"
    monkeypatch.setattr(carga, "dentro_da_janela", lambda agora: (True, "00:00-06:00"))
    for ck in db.scalars(select(CheckpointCarga).where(CheckpointCarga.sistema == "SEI")):
        db.delete(ck)  # banco de teste: cada teste parte sem checkpoint
    db.commit()


def _ck(db):
    db.expire_all()
    return db.scalar(select(CheckpointCarga).where(CheckpointCarga.sistema == "SEI"))


def _inc(db, api, dia=29):
    return carga.executar_incremental(db, "zz@aion.local", _noite(dia), transport=api.transport(),
                                      dormir=lambda s: None)


def _logs(db, ex):
    return [lg.mensagem for lg in db.scalars(select(Log).where(Log.execucao_id == ex.id))]


def _detalhes(api):
    return [c for c in api.gets() if c.url.path.startswith("/processos/")]


# ------------------------------------------------------------------ B6 / B5 (unitários)
def test_b6_protocolo_so_digitos():
    assert pj.digitos("99990.000001/2026-01") == pj.digitos("99990 000001 2026 01") == "99990000001202601"
    assert pj.digitos(None) is None and pj.digitos("./-") is None
    pub = pj.Publicos(por_id={"9000001": "99990000001202601"}, por_protocolo={"99990000001202601": "9000001"})
    a = {"idAndamento": "A1", "idProcesso": "9000001", "protocoloProcedimento": "99990000001202601"}
    assert pj.projeta_andamento(a, "oficial", pub)["id_andamento"] == "A1"


def test_b5_id_e_protocolo_do_mesmo_processo_publico():
    pub = pj.Publicos(por_id={"9000001": "111"}, por_protocolo={"111": "9000001"})
    np_ = pj.NaoPublicos(ids=frozenset({"9000002"}), protocolos=frozenset({"222"}))
    ok = {"idProcesso": "9000001", "protocoloProcedimento": "1.1.1"}
    id_restrito_prot_publico = {"idProcesso": "9000002", "protocoloProcedimento": "111"}
    id_publico_prot_restrito = {"idProcesso": "9000001", "protocoloProcedimento": "222"}
    id_publico_prot_outro = {"idProcesso": "9000001", "protocoloProcedimento": "333"}
    sem_prot = {"idProcesso": "9000001"}
    assert pj.classifica_andamento(ok, pub, np_) == pj.PUBLICO_OK
    assert pj.classifica_andamento(id_restrito_prot_publico, pub, np_) == pj.RESTRITO
    assert pj.classifica_andamento(id_publico_prot_restrito, pub, np_) == pj.RESTRITO
    assert pj.classifica_andamento(id_publico_prot_outro, pub, np_) == pj.INCONSISTENTE
    assert pj.classifica_andamento(sem_prot, pub, np_) == pj.INCONSISTENTE
    for a in (id_restrito_prot_publico, id_publico_prot_restrito, id_publico_prot_outro, sem_prot):
        assert pj.projeta_andamento({"idAndamento": "A", **a}, "oficial", pub, np_) is None


def test_b5_integrado_id_restrito_com_protocolo_publico_nao_grava(monkeypatch, db):
    _prep(monkeypatch, db, amostra=sm.Sei(niveis={2: "1"}))
    api = sm.Sei(andamentos=4, processo_andamento=lambda i: 2, protocolo_andamento=lambda i: "99990.000001/2026-01")
    ex = _inc(db, api)
    assert "andamentos_restrito_conhecido=4" in ex.mensagem and ex.status == "sucesso"
    ids = {r.dados.get("id_processo") for r in db.scalars(select(RegistroBronze).where(
        RegistroBronze.sistema_origem == "SEI", RegistroBronze.tabela == "andamentos"))}
    assert "9000002" not in ids


def test_b6_integrado_protocolo_com_outra_formatacao_grava(monkeypatch, db):
    _prep(monkeypatch, db)
    api = sm.Sei(andamentos=3, protocolo_andamento=lambda i: "99990000001202601")
    ex = _inc(db, api)
    assert ex.status == "sucesso" and ex.lidos == 3 and "andamentos_nao_gravados_sigilo=0" in ex.mensagem
    assert not _detalhes(api)  # pai já público: não consulta


# ------------------------------------------------------------------ M4: pai desconhecido
def test_m4_pai_desconhecido_publico_e_consultado_e_gravado(monkeypatch, db):
    _prep(monkeypatch, db)
    api = sm.Sei(andamentos=4, processo_andamento=lambda i: 603)
    ex = _inc(db, api)
    assert ex.status == "sucesso" and "pais_publicos=1" in ex.mensagem and len(_detalhes(api)) == 1
    assert _ck(db).ultima_chave == ["2026-10-29"]
    assert "9000603" in carga.processos_publicos(db, "oficial").por_id
    pend = db.scalar(select(SeiPaiPendente).where(SeiPaiPendente.id_processo == "9000603"))
    assert pend.situacao == "publico" and pend.tentativas == 1


def test_m4_marcador_restrito_so_id_protocolo_e_nivel(monkeypatch, db):
    """Tester (1): da consulta do pai restrito nada além de id, protocolo (dígitos) e nível chega à bronze/log."""
    _prep(monkeypatch, db)
    api = sm.Sei(andamentos=4, processo_andamento=lambda i: 501, niveis={501: "1"})
    ex = _inc(db, api)
    assert ex.status == "sucesso" and "pais_nao_publicos=1" in ex.mensagem and _ck(db).ultima_chave == ["2026-10-29"]
    m = db.scalar(select(SeiProcessoNaoPublico).where(SeiProcessoNaoPublico.id_processo == "9000501"))
    assert (m.protocolo_digitos, m.nivel_acesso, m.ambiente_origem) == ("99990000501202601", "1", "oficial")
    assert set(SeiProcessoNaoPublico.__table__.columns.keys()) == {
        "id", "id_processo", "protocolo_digitos", "nivel_acesso", "ambiente_origem", "atualizado_em"}
    vazou = [r for r in db.scalars(select(RegistroBronze).where(RegistroBronze.sistema_origem == "SEI"))
             if "000501" in str(r.dados) or r.id_origem == "9000501"]
    assert vazou == []
    for msg in _logs(db, ex):
        assert "000501" not in msg and "ZZTESTE" not in msg and "00.01" not in msg and sm.CPF_OK not in msg
    aud = db.scalars(select(Auditoria).where(Auditoria.alvo == f"execucao:{ex.id}")).all()
    assert all("ZZTESTE" not in str(a.detalhe) and sm.CPF_OK not in str(a.detalhe) for a in aud)


def test_m4_tres_tentativas_persistem_e_depois_alerta(monkeypatch, db):
    """Tester (2)(3): contador por pai persiste entre execuções; após 3, parcial com alerta, nunca descarte mudo."""
    _prep(monkeypatch, db)
    for n in (1, 2, 3, 4):
        api = sm.Sei(andamentos=2, processo_andamento=lambda i: 602, inexistentes={602})
        ex = _inc(db, api)
        assert ex.status == "parcial" and "andamentos_pai_desconhecido=2" in ex.mensagem
        assert "checkpoint_avancou=False" in ex.mensagem and _ck(db) is None
        pend = db.scalar(select(SeiPaiPendente).where(SeiPaiPendente.id_processo == "9000602"))
        db.refresh(pend)
        assert pend.tentativas == min(n, 3) and pend.situacao == "pendente"
        assert len(_detalhes(api)) == (1 if n <= 3 else 0)  # 4ª execução não consulta de novo
        if n >= 3:
            assert "alerta=pai_desconhecido_esgotado" in ex.mensagem
            assert any("pai_desconhecido_esgotado=1" in m for m in _logs(db, ex))
        else:
            assert "pai_desconhecido_esgotado" not in ex.mensagem
    assert all("000602" not in m for m in _logs(db, ex))


def test_m4_regressao_qa_20_a_29_outubro_teto_1_pagina(monkeypatch, db):
    """Cenário do QA: checkpoint em 20/10, rodadas 21, 25 e 29/10, teto de 1 página, só restrito conhecido."""
    _prep(monkeypatch, db, paginas="1", amostra=sm.Sei(niveis={2: "1"}))
    db.add(CheckpointCarga(sistema="SEI", tabela="andamentos", posicao=0, ultima_chave=["2026-10-20"],
                           concluida=True))
    db.commit()
    starts, chaves = [], []
    for dia in (21, 25, 29):
        api = sm.Sei(andamentos=70, processo_andamento=lambda i: 2 if i % 3 == 0 else 1)
        ex = _inc(db, api, dia)
        starts.append([(c.url.params["dataInicio"], c.url.params["dataFim"], int(c.url.params["start"]))
                       for c in api.gets() if c.url.path == "/andamentos/completo"])
        chaves.append(_ck(db).ultima_chave)
        assert "checkpoint_avancou=True" in ex.mensagem and "andamentos_restrito_conhecido=" in ex.mensagem
        assert not _detalhes(api)
    assert starts == [[("20/10/2026", "21/10/2026", 0)], [("20/10/2026", "21/10/2026", 50)],
                      [("21/10/2026", "29/10/2026", 0)]]
    assert chaves == [["2026-10-20", "2026-10-21", 50], ["2026-10-21"], ["2026-10-21", "2026-10-29", 50]]


# ------------------------------------------------------------------ reclassificação público -> restrito
def test_reclassificacao_publico_vira_restrito_some_sem_delete(monkeypatch, db):
    from app.explorador import servico as exp
    _prep(monkeypatch, db, amostra=sm.Sei(n_processos=4))
    api = sm.Sei(andamentos=3, processo_andamento=lambda i: 4)
    assert _inc(db, api).status == "sucesso"

    def linhas_4():
        db.expire_all()
        return [r for r in db.scalars(select(RegistroBronze).where(RegistroBronze.sistema_origem == "SEI"))
                if r.dados.get("id_procedimento") == "9000004" or r.dados.get("id_processo") == "9000004"]
    antes = linhas_4()
    assert {r.tabela for r in antes} == {"processos", "documentos", "andamentos"}
    total = db.scalar(select(func.count()).select_from(RegistroBronze))
    am = carga.executar_amostra(db, "zz@aion.local", transport=sm.Sei(n_processos=4, niveis={4: "1"}).transport(),
                                dormir=lambda s: None)
    assert "reclassificados=1" in am.mensagem
    depois = linhas_4()
    assert db.scalar(select(func.count()).select_from(RegistroBronze)) == total  # nada apagado
    assert {r.id for r in depois} == {r.id for r in antes}
    assert all(r.quarentena == carga.QUAR_RECLASS for r in depois)
    assert "9000004" not in carga.processos_publicos(db, "oficial").por_id
    aud = db.scalar(select(Auditoria).where(Auditoria.acao == "sei_reclassificacao").order_by(Auditoria.id.desc()))
    assert aud.detalhe["id_processo"] == "9000004" and aud.detalhe["andamentos_ocultados"] >= 3
    assert "ZZTESTE" not in str(aud.detalhe)
    for r in depois:  # explorador não mostra nem revela as linhas ocultadas
        assert exp.amostra(db, "SEI", "bronze", r.tabela, 1, {"id": str(r.id)})["linhas"] == []
    # downstream: andamento novo do processo reclassificado é descartado e não segura o checkpoint
    api2 = sm.Sei(andamentos=2, processo_andamento=lambda i: 4)
    ex = _inc(db, api2)
    assert ex.status == "sucesso" and "andamentos_restrito_conhecido=2" in ex.mensagem


def test_0023_aditiva_e_downgrade_sem_efeito():
    import importlib.util
    import pathlib
    p = pathlib.Path(__file__).parents[1] / "alembic" / "versions" / "0023_sei_marcador_reclassificacao.py"
    spec = importlib.util.spec_from_file_location("m0023", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    corpo = p.read_text()
    assert mod.down_revision == "0022" and "DROP" not in corpo and "DELETE" not in corpo.upper().split("DEF UPGRADE")[1]
    assert mod.downgrade() is None


@pytest.mark.parametrize("v", [None, "", "false"])
def test_carga_liberada_desligada_por_padrao(monkeypatch, v):
    from app.connectors.sei.config import ConfigSei
    sm.env_oficial(monkeypatch, CARGA_LIBERADA=v)
    assert ConfigSei.do_ambiente().carga_liberada is False
