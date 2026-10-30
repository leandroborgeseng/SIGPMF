"""PR-A (CT15/UXP9 e agenda desligada). Dados sintéticos ZZTESTE; só leitura do controle (nunca a origem)."""
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete

from app.models.controle import Agendamento, Execucao
from app.services import agenda as agm
from app.services import progresso as pg
from app.services import tipo_execucao as te
from tests.test_zzzzzzzzzzzzzzzzzzzzzzz_painel_progresso import _cfg

SIS = "ZZTESTE"
AGORA = datetime(2026, 10, 30, 0, 29, tzinfo=UTC)  # 21:29 BRT


@pytest.fixture
def limpo(db):
    def zera():
        db.execute(delete(Execucao).where(Execucao.sistema == SIS))
        db.execute(delete(Agendamento).where(Agendamento.conector == "zzteste"))
        db.commit()
    zera()
    yield db
    zera()


def _ex(db, **kw):
    base = dict(tipo="carga_completa_conector", sistema=SIS, status="sucesso", usuario="zzteste@aion.local",
                iniciado_em=AGORA - timedelta(minutes=5), finalizado_em=AGORA - timedelta(minutes=4), lidos=0, lotes=0)
    base.update(kw)
    e = Execucao(**base)
    db.add(e)
    db.commit()
    return e


@pytest.mark.parametrize("tipo,modo,esperado", [
    ("amostra_conector", None, "Amostra"), ("enriquecimento_sil", None, "Amostra"),
    ("carga_municipe_amostra", None, "Amostra"), ("amostra_gestaoescolar", None, "Amostra"),
    ("amostra_conector", "completa", "Amostra"),  # amostra nunca vira "Carga completa", mesmo com modo preenchido
    ("carga_completa_conector", "incremental", "Incremental"), ("carga_completa_conector", "completa", "Carga completa"),
    ("carga_completa_conector", None, "Carga completa")])
def test_ct15_tipo_real_da_execucao(limpo, tipo, modo, esperado):
    e = _ex(limpo, tipo=tipo, modo=modo, lidos=10, lotes=1)
    assert te.tipo_texto(e) == esperado
    f = pg.Fonte("zzteste", "ZZTESTE", (SIS,), (SIS,), None, None)
    assert pg.fonte(limpo, f, AGORA)["ultima_execucao"]["modo"] == esperado


def test_ct15_amostra_em_andamento_nao_aparece_como_carga_completa(limpo):
    _ex(limpo, tipo="amostra_conector", status="em_andamento", finalizado_em=None, atualizado_em=AGORA)
    f = pg.Fonte("zzteste", "ZZTESTE", (SIS,), (SIS,), None, None)
    assert pg.fonte(limpo, f, AGORA)["progresso"]["modo"] == "Amostra"


def test_uxp9_parou_no_limite_de_lotes(limpo):
    e = _ex(limpo, tipo="enriquecimento_sil", lidos=49, lotes=1,
            mensagem="ambiente=sandbox host=- endpoint=empresas lidos=49 localizados=49 nao_localizados=0 erros=0 "
                     "lotes=1 repetidos=0 fim=max_lotes proxima_sequencia=50")
    assert te.texto_limite(e) == "Amostra: 49 linhas (parou no limite de 1 lote). Não é o total da origem."
    f = pg.Fonte("zzteste", "ZZTESTE", (SIS,), (SIS,), None, None)
    assert pg.fonte(limpo, f, AGORA)["ultima_execucao"]["texto_limite"] == te.texto_limite(e)
    # amostra genérica da SIL (parcial do SilConnector)
    g = _ex(limpo, tipo="amostra_conector", lidos=1000, lotes=2, mensagem="parcial: 2 lote(s) lido(s), há mais na origem. lidos=1000")
    assert te.texto_limite(g) == "Amostra: 1.000 linhas (parou no limite de 2 lotes). Não é o total da origem."
    # sem teto: sem aviso
    h = _ex(limpo, tipo="amostra_conector", lidos=5, mensagem="lotes=1 fim=fim_sequencia")
    assert te.texto_limite(h) is None


def test_agenda_desligada_com_executar_agora_pendente_nao_mostra_proxima(limpo, monkeypatch):
    """Bug do SIL: desligada com `forcar` pendente mostrava "próxima às 21:29" (a hora atual)."""
    _cfg(monkeypatch, agenda_habilitada=True, agenda_janela="00:00-06:00")
    ag = Agendamento(conector="zzteste", horario="05:30", ativo=False, forcar=True, ordem=99)
    limpo.add(ag)
    limpo.commit()
    assert agm.proxima(ag, AGORA, 0, 360) is None
    f = pg.Fonte("sil", "SIL", (SIS,), (SIS,), None, "zzteste")
    r = pg.fonte(limpo, f, AGORA)
    assert r["status"] == "pausada" and r["motivo"] == "Agenda desligada"
    assert r["aguardando_em"] is None and r["aguardando_em_brt"] is None


def test_agenda_desligada_no_ambiente_mostra_agenda_desligada(limpo, monkeypatch):
    _cfg(monkeypatch, agenda_habilitada=False)
    limpo.add(Agendamento(conector="zzteste", horario="05:30", ativo=True, ordem=99))
    limpo.commit()
    r = pg.fonte(limpo, pg.Fonte("sil", "SIL", (SIS,), (SIS,), None, "zzteste"), AGORA)
    assert r["motivo"] == "Agenda desligada" and r["aguardando_em_brt"] is None


def test_agenda_ligada_segue_com_proxima(limpo, monkeypatch):
    _cfg(monkeypatch, agenda_habilitada=True, agenda_janela="00:00-06:00")
    limpo.add(Agendamento(conector="zzteste", horario="02:30", ativo=True, ordem=99))
    limpo.commit()
    r = pg.fonte(limpo, pg.Fonte("sil", "SIL", (SIS,), (SIS,), None, "zzteste"), AGORA)
    assert r["status"] == "aguardando" and r["aguardando_em_brt"] == "02:30"
