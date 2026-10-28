"""SIL15 (forcar não liga agendamento desligado) e SIL17 (janela que atravessa a meia-noite). Sem rede, sem dado real."""
from datetime import date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.core.config import get_settings
from app.models.controle import Agendamento
from app.services import agenda as ag

SP = ZoneInfo("America/Sao_Paulo")


def _agd(**kw):
    base = dict(conector="sil", horario="05:30", ativo=False, ordem=6, forcar=False, ultima_data=None,
                tentativas=0, proxima_tentativa_em=None)
    base.update(kw)
    return Agendamento(**base)


def test_sil15_forcar_nao_liga_agendamento_desligado():
    ini, fim = ag.janela("00:00-06:00")
    meio_dia = datetime(2026, 10, 28, 12, 0, tzinfo=SP)
    assert ag.devido(_agd(ativo=False, forcar=True), meio_dia, ini, fim) is False
    assert ag.devido(_agd(ativo=True, forcar=True), meio_dia, ini, fim) is True


def test_sil17_janela_atravessa_meia_noite_dispara_uma_vez_e_refaz_falha():
    ini, fim = ag.janela("22:00-06:00")
    assert ag.devido(_agd(ativo=True, horario="01:00"), datetime(2026, 10, 28, 22, 30, tzinfo=SP), ini, fim) is False
    assert ag.devido(_agd(ativo=True, horario="01:00"), datetime(2026, 10, 29, 1, 5, tzinfo=SP), ini, fim) is True
    feito = _agd(ativo=True, horario="23:00", ultima_data=date(2026, 10, 28))
    assert ag.devido(feito, datetime(2026, 10, 29, 0, 30, tzinfo=SP), ini, fim) is False
    assert ag.devido(feito, datetime(2026, 10, 29, 5, 0, tzinfo=SP), ini, fim) is False
    assert ag.devido(feito, datetime(2026, 10, 29, 23, 1, tzinfo=SP), ini, fim) is True
    falhou = _agd(ativo=True, horario="23:00", tentativas=1, ultima_data=date(2026, 10, 27))
    assert ag.devido(falhou, datetime(2026, 10, 29, 0, 30, tzinfo=SP), ini, fim) is True
    assert ag.dia_da_janela(datetime(2026, 10, 29, 0, 30, tzinfo=SP), ini, fim) == date(2026, 10, 28)
    assert ag.dia_da_janela(datetime(2026, 10, 29, 0, 30, tzinfo=SP), *ag.janela("00:00-06:00")) == date(2026, 10, 29)


def test_sil17_encerra_marca_a_data_da_janela(db, monkeypatch):
    monkeypatch.setenv("AGENDA_JANELA", "22:00-06:00")
    get_settings.cache_clear()
    item = db.scalar(select(Agendamento).where(Agendamento.conector == "sil"))
    antes = (item.ultima_data, item.tentativas, item.proxima_tentativa_em, item.ultimo_status)
    try:
        monkeypatch.setattr(ag, "alertar", lambda *a, **k: None)
        ag._encerra(db, item, "sucesso", "ok", datetime(2026, 10, 29, 0, 40, tzinfo=SP), resolvido=True)
        assert item.ultima_data == date(2026, 10, 28)
    finally:
        item.ultima_data, item.tentativas, item.proxima_tentativa_em, item.ultimo_status = antes
        db.commit()
        get_settings.cache_clear()
