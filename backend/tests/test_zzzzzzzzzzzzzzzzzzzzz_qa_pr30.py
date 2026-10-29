"""QA PR30 (91b7969, repro do Tester, agora com asserts): só mock .invalid + ZZTESTE."""
import httpx
import pytest
from sqlalchemy import select, update

from app.bronze.ingestao import ingere
from app.models.controle import Execucao
from app.services import carga_completa as ccomp
from app.services import carga_conector as cc
from tests.test_zzzzzzzzzzzzzzzz_sil_unificado import PROD, SANDBOX, Api, _checkpoint_sil, _zera_checkpoint_sil, sil_mock  # noqa: F401
from tests.test_zzzzzzzzzzzzzzzzzz_sil_agenda_amostra import _env_sil, _limpa_sil


def test_qa_401_na_oficial_nao_cai_para_sandbox(monkeypatch):
    from app.connectors.sil.conector import ErroSil, SilConnector
    from app.connectors.sil.config import ConfigSil
    _env_sil(monkeypatch)
    for st in (401, 403):
        api = Api(auth_status={PROD: st})
        with pytest.raises(ErroSil):
            SilConnector(ConfigSil.do_ambiente(), transporte=httpx.MockTransport(api), esperar=lambda s: None).ler_amostra("Empresas")
        assert {h[0] for h in api.hits} <= {PROD}, api.hits
    _env_sil(monkeypatch, "true")
    api = Api(auth_status={SANDBOX: 401})
    with pytest.raises(ErroSil):
        SilConnector(ConfigSil.do_ambiente(), transporte=httpx.MockTransport(api), esperar=lambda s: None).ler_amostra("Empresas")
    assert {h[0] for h in api.hits} <= {SANDBOX}


def test_qa_gate_com_so_amostra_e_bronze_sandbox(db, monkeypatch):
    _limpa_sil(db)
    _env_sil(monkeypatch, "true")
    ex = Execucao(tipo="enriquecimento_sil", sistema="SIL", status="sucesso", usuario="zz@aion.local")
    db.add(ex)
    db.commit()
    ingere(db, "SIL", "empresas", "_id_sigpmf", None, [{"_id_sigpmf": "zz-qa30", "resposta": {"Nome": "ZZTESTE"}}], ex.id)
    db.commit()
    _env_sil(monkeypatch)  # volta para a oficial
    motivo = ccomp.motivo_bloqueio(db, "sil")
    print("QA_GATE_SO_SANDBOX motivo=", motivo)
    assert motivo == ccomp.SEM_AMOSTRA                              # M1
    db.execute(update(Execucao).where(Execucao.id == ex.id).values(status="interrompida"))
    db.commit()
    _limpa_sil(db)


@pytest.mark.usefixtures("sil_mock")
def test_qa_checkpoint_sandbox_vaza_para_oficial(db, monkeypatch):
    from app.connectors.sil.conector import SilConnector
    from app.connectors.sil.config import ConfigSil
    _limpa_sil(db)
    _zera_checkpoint_sil(db)
    api = Api(ccms=range(1, 151))
    monkeypatch.setattr(cc, "leitor_de", lambda conector, cfg: SilConnector(
        ConfigSil.do_ambiente(), transporte=httpx.MockTransport(api), esperar=lambda s: None))
    _env_sil(monkeypatch, "true")
    a = cc.executar_amostra(db, "admin@aion.local", "sil")
    ccomp.iniciar(db, "admin@aion.local", "sil")
    cp = _checkpoint_sil(db)
    print("QA_CP_APOS_SANDBOX", cp.ultima_chave if cp else None, a.status)
    _env_sil(monkeypatch)
    assert ccomp.motivo_bloqueio(db, "sil") == ccomp.SEM_AMOSTRA    # M1: amostra da homologação não libera a oficial
    cc.executar_amostra(db, "admin@aion.local", "sil")             # amostra na oficial
    api.sequencias.clear()
    api.hits.clear()
    ccomp.iniciar(db, "admin@aion.local", "sil")
    print("QA_OFICIAL_PRIMEIRA_SEQ", api.sequencias[:3], {h[0] for h in api.hits})
    assert cp is not None and cp.ambiente == "sandbox"
    assert api.sequencias[0] == 1                                   # A1: oficial começa do início (sem checkpoint), não do 51
    assert {h[0] for h in api.hits} <= {PROD}
    _zera_checkpoint_sil(db)
    _limpa_sil(db)


def test_0020_nao_toca_em_outra_execucao_tipo_35(db):
    """A run 35 (oficial) já gravou 48 linhas 'producao': a 0020 filtra só execucao_id IN (25,31)."""
    from app.services.marca_sandbox_sil import marcar
    from tests.test_zzzzzzzzzzzzzzzzzz_sil_agenda_amostra import _ambientes, _cenario
    alvo, outra = _cenario(db, novos=(2, 48))
    marcar(db.connection(), (alvo,))
    db.commit()
    assert _ambientes(db, (alvo,)) == ["sandbox"] * 2
    assert _ambientes(db, (outra,)) == ["producao"] * 48
    _limpa_sil(db)


def test_low_linha_oficial_com_mesmo_id_da_sandbox_conta_como_novo(db, monkeypatch):
    _limpa_sil(db)
    ex = Execucao(tipo="amostra_conector", sistema="SIL", status="sucesso", usuario="zz@aion.local")
    db.add(ex)
    db.commit()
    _env_sil(monkeypatch, "true")
    ingere(db, "SIL", "Empresas", "Ccm", None, [{"Ccm": 9101, "Nome": "ZZTESTE"}], ex.id)
    _env_sil(monkeypatch)
    c = ingere(db, "SIL", "Empresas", "Ccm", None, [{"Ccm": 9101, "Nome": "ZZTESTE"}], ex.id)
    assert c["novos"] == 1 and c["atualizados"] == 0
    db.commit()
    _limpa_sil(db)


def test_0021_rotula_checkpoint_legado_como_sandbox_e_oficial_vazio(db, monkeypatch):
    """Simula o banco antes da 0021 (só no *_test): checkpoint SIL [49] sem ambiente -> vira 'sandbox' auditado."""
    from alembic import command
    from sqlalchemy import text

    from app.models.controle import Auditoria, CheckpointCarga
    from tests.conftest import alembic_cfg
    db.execute(text("DELETE FROM controle.checkpoints_carga WHERE sistema='SIL'"))  # só banco de teste
    db.execute(text("ALTER TABLE controle.checkpoints_carga DROP CONSTRAINT uq_checkpoint_sistema_tabela_ambiente"))
    db.execute(text("ALTER TABLE controle.checkpoints_carga DROP COLUMN ambiente"))
    db.execute(text("ALTER TABLE controle.checkpoints_carga ADD CONSTRAINT uq_checkpoint_sistema_tabela "
                    "UNIQUE (sistema, tabela)"))
    db.execute(text("INSERT INTO controle.checkpoints_carga (sistema, tabela, posicao, ultima_chave, concluida) "
                    "VALUES ('SIL', 'Empresas', 49, '[49]', false)"))
    db.commit()
    db.close()
    cfg = alembic_cfg()
    command.stamp(cfg, "0020")
    command.upgrade(cfg, "head")
    command.upgrade(cfg, "head")                                   # idempotente
    db.expire_all()
    cps = list(db.scalars(select(CheckpointCarga).where(CheckpointCarga.sistema == "SIL")))
    assert [(c.ambiente, c.ultima_chave, c.posicao) for c in cps] == [("sandbox", [49], 49)]
    aud = db.scalars(select(Auditoria).where(Auditoria.acao == "checkpoint_relabel")).all()
    assert len(aud) == 1 and aud[0].detalhe["checkpoints"][0]["posicao"] == 49
    _env_sil(monkeypatch)
    assert _checkpoint_sil(db) is None                              # produção sem checkpoint: começa do início
    db.commit()
