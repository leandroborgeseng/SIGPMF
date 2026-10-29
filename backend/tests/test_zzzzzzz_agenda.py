# Roda por último (zzzzzzz): grava bronze SAUDE/CEMITERIO pela carga completa. Só dados sintéticos.
"""Agenda das cargas completas (AG1–AG6): janela, horário, uma por vez, pular sem mudança, tentativas e API."""
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select, update

from app.connectors.sqlserver.politicas import TabelaRecusada, politica
from app.core import recursos
from app.core.config import get_settings
from app.core.sql_guard import garante_somente_leitura
from app.models.controle import Agendamento, Auditoria, Execucao
from app.services import agenda as ag
from app.services import carga_completa as ccomp
from app.services import carga_conector as cc
from tests.test_zz_carga_conector import Leitor
from tests.test_zzz_carga_completa import Origem

SP = ZoneInfo("America/Sao_Paulo")


def _local(ano, mes, dia, h, m=0):
    return datetime(ano, mes, dia, h, m, tzinfo=SP).astimezone(UTC)


class OrigemAgenda(Origem):
    """Origem sintética da carga completa + assinatura (COUNT/MAX) calculada das mesmas linhas."""
    def __init__(self, sistema):
        super().__init__(sistema)
        self.assinaturas, self.falhar_assinatura, self.desdes, self._filtro = 0, False, [], None

    def ler_pagina(self, conn, tabela, politica, ordem, ultima, limite, desde=None):
        """IN2: com `desde`, a página usa o SQL real com o filtro e a origem sintética aplica o mesmo filtro."""
        self.desdes.append((tabela, desde))
        if desde is not None:
            sql, params = self.montar_pagina(politica, tabela, ordem, limite, ultima, desde=desde)
            assert f"[{desde[0]}] >= %s" in sql and params[-1] == desde[1]
        self._filtro = desde
        try:
            return super().ler_pagina(conn, tabela, politica, ordem, ultima, limite)
        finally:
            self._filtro = None

    def linhas(self, tabela):
        rows = super().linhas(tabela)
        f = getattr(self, "_filtro", None)
        if f is None:
            return rows
        campo, valor = f
        return [r for r in rows if r.get(campo) is not None and str(r.get(campo)) >= valor.strftime("%Y-%m-%d %H:%M:%S")]

    def assinatura(self, conn, tabela, pol, campo_incremental):
        sql = self.montar_assinatura(pol, tabela, campo_incremental)  # SQL real, pelo guard
        assert sql.startswith("SELECT COUNT(*)")
        if self.falhar_assinatura:
            raise TimeoutError("timeout sintético em srv-sintetico.invalid")
        self.assinaturas += 1
        linhas = self.linhas(tabela)
        vals = [str(r.get(campo_incremental)) for r in linhas if campo_incremental and r.get(campo_incremental) is not None]
        return [len(linhas), max(vals) if vals else None]


@pytest.fixture
def origem(monkeypatch):
    o = {"SAUDE": OrigemAgenda("SAUDE"), "CEMITERIO": OrigemAgenda("CEMITERIO")}
    monkeypatch.setenv("CARGA_COMPLETA_LOTE", "50")
    monkeypatch.setenv("CARGA_COMPLETA_PAUSA_MS", "0")
    monkeypatch.setenv("AGENDA_JANELA", "00:00-24:00")
    monkeypatch.setattr(recursos, "disco", lambda: recursos.Uso(total=100, livre=50))
    get_settings.cache_clear()
    monkeypatch.setattr(ccomp, "leitor_padrao", lambda conector, db: o[cc.CONECTORES[conector][0]])

    def sincrono(ex_id, conector, trava, somente_camadas=False):
        ccomp.rodar(ex_id, conector, trava, leitor=o[cc.CONECTORES[conector][0]], somente_camadas=somente_camadas)
    monkeypatch.setattr(ccomp, "disparar", sincrono)
    yield o
    get_settings.cache_clear()


def _leitores(o):
    return {"saude": o["SAUDE"], "cemiterio": o["CEMITERIO"]}


def _esperar(db, ex_id, parar=None):
    db.expire_all()
    return db.get(Execucao, ex_id)


def _zera_agenda(db, ativo=True):
    """Liga (ou desliga) só Saúde e Cemitério; os conectores tributários ficam desligados nestes testes."""
    db.execute(update(Agendamento).values(ativo=False, forcar=False, ultima_data=None, tentativas=0,
                                          proxima_tentativa_em=None, ultimo_status=None, ultima_mensagem=None,
                                          assinatura_origem=None, ultima_execucao_id=None))
    db.execute(update(Agendamento).where(Agendamento.conector.in_(("saude", "cemiterio"))).values(ativo=ativo))
    db.commit()


def _amostras(db):
    for conector in ("saude", "cemiterio"):
        ex = cc.executar_amostra(db, "admin@aion.local", conector, leitor=Leitor(cc.CONECTORES[conector][0]))
        assert ex.status in ("sucesso", "sem_novidades"), ex.mensagem


def _ag(db, conector):
    db.expire_all()
    return db.scalar(select(Agendamento).where(Agendamento.conector == conector))


# ---------------------------------------------------------------- regras puras

def test_ag2_janela_e_dia_todo():
    assert ag.janela("00:00-06:00") == (0, 360)
    assert ag.janela("00:00-24:00") == (0, 1440) and ag.dia_todo(0, 1440)
    ini, fim = ag.janela("22:00-06:00")  # atravessa a meia-noite
    assert ag.na_janela(23 * 60, ini, fim) and ag.na_janela(60, ini, fim) and not ag.na_janela(12 * 60, ini, fim)
    assert ag.na_janela(5 * 60 + 59, 0, 360) and not ag.na_janela(6 * 60, 0, 360)
    for ruim in ("", "0-6", "25:00-06:00", "00:00-24:01", "00:60-06:00"):
        with pytest.raises(ag.AgendaInvalida):
            ag.janela(ruim)


def test_ag1_devido_horario_janela_forcar_e_espera():
    a = Agendamento(conector="saude", horario="01:00", ativo=True, forcar=False, ultima_data=None, tentativas=0)
    ini, fim = 0, 360
    assert not ag.devido(a, _local(2026, 10, 28, 0, 30), ini, fim)       # antes do horário
    assert ag.devido(a, _local(2026, 10, 28, 1, 0), ini, fim)            # no horário, dentro da janela
    assert not ag.devido(a, _local(2026, 10, 28, 7, 0), ini, fim)        # fora da janela
    a.ultima_data = date(2026, 10, 28)
    assert not ag.devido(a, _local(2026, 10, 28, 2, 0), ini, fim)        # já resolvido hoje
    assert ag.devido(a, _local(2026, 10, 29, 1, 5), ini, fim)            # dia seguinte
    a.proxima_tentativa_em = _local(2026, 10, 29, 2, 0)
    assert not ag.devido(a, _local(2026, 10, 29, 1, 30), ini, fim)       # esperando nova tentativa
    a.ativo = False
    assert not ag.devido(a, _local(2026, 10, 29, 3, 0), ini, fim)
    a.forcar = True
    assert not ag.devido(a, _local(2026, 10, 29, 12, 0), ini, fim)       # SIL15: forçar não liga um desligado
    a.ativo = True
    assert ag.devido(a, _local(2026, 10, 29, 12, 0), ini, fim)           # executar agora ignora horário e janela


def test_ag1_proxima_execucao_para_a_tela():
    a = Agendamento(conector="saude", horario="01:00", ativo=True, forcar=False, ultima_data=None, tentativas=0)
    p = ag.proxima(a, _local(2026, 10, 28, 0, 10), 0, 360)
    assert p.astimezone(SP).strftime("%d %H:%M") == "28 01:00"
    p = ag.proxima(a, _local(2026, 10, 28, 9, 0), 0, 360)                # perdeu a janela hoje -> amanhã
    assert p.astimezone(SP).strftime("%d %H:%M") == "29 01:00"
    a.ultima_data = date(2026, 10, 28)
    p = ag.proxima(a, _local(2026, 10, 28, 2, 0), 0, 360)
    assert p.astimezone(SP).strftime("%d %H:%M") == "29 01:00"
    a.ativo = False
    assert ag.proxima(a, _local(2026, 10, 28, 2, 0), 0, 360) is None


def test_ag3_sql_da_assinatura_passa_pelo_guard_e_respeita_a_politica():
    o = Origem("SAUDE")
    pol = politica("SAUDE")
    sql = o.montar_assinatura(pol, "pacientes", "dt_ultimaatual")
    assert sql == garante_somente_leitura(sql) and "MAX([dt_ultimaatual])" in sql
    assert "MAX(" not in o.montar_assinatura(pol, "pacientes", "ColunaForaDaPolitica")
    with pytest.raises(TabelaRecusada):
        o.montar_assinatura(pol, "TabelaNaoAutorizada", None)


# ---------------------------------------------------------------- ciclo completo

def test_ag2_ciclo_roda_uma_por_vez_na_ordem_e_resolve_o_dia(db, origem):
    _amostras(db)
    _zera_agenda(db)
    antes = db.scalar(select(func.max(Execucao.id))) or 0
    agora = _local(2026, 10, 28, 5, 0)
    feitos = ag.ciclo(db, agora=agora, leitores=_leitores(origem), esperar=_esperar)
    assert feitos == [("saude", "sucesso"), ("cemiterio", "sucesso")]
    exs = list(db.scalars(select(Execucao).where(Execucao.id > antes, Execucao.usuario == ag.USUARIO).order_by(Execucao.id)))
    assert [e.sistema for e in exs] == ["SAUDE", "CEMITERIO"] and all(e.status == "sucesso" for e in exs)
    assert exs[0].finalizado_em <= exs[1].iniciado_em  # uma por vez
    s = _ag(db, "saude")
    assert s.ultima_data == date(2026, 10, 28) and s.ultimo_status == "sucesso" and s.assinatura_origem
    assert s.ultima_execucao_id == exs[0].id
    # mesmo dia: nada a fazer
    assert ag.ciclo(db, agora=agora + timedelta(minutes=30), leitores=_leitores(origem), esperar=_esperar) == []


def test_ag3_origem_sem_mudanca_nao_le_nada(db, origem):
    _amostras(db)
    _zera_agenda(db)
    ag.ciclo(db, agora=_local(2026, 10, 28, 5, 0), leitores=_leitores(origem), esperar=_esperar)
    paginas, total = origem["SAUDE"].paginas, db.scalar(select(func.count()).select_from(Execucao))
    feitos = ag.ciclo(db, agora=_local(2026, 10, 29, 5, 0), leitores=_leitores(origem), esperar=_esperar)
    assert feitos == [("saude", "sem_mudanca"), ("cemiterio", "sem_mudanca")]
    assert origem["SAUDE"].paginas == paginas                                     # nenhuma página lida
    assert db.scalar(select(func.count()).select_from(Execucao)) == total          # nenhuma carga iniciada
    assert _ag(db, "saude").ultima_data == date(2026, 10, 29)
    # origem mudou (linha nova): roda de novo
    pac = origem["SAUDE"].linhas("pacientes")[0]
    origem["SAUDE"].extras["pacientes"] = [{**pac, "Matricula": 999999, "dt_ultimaatual": "2030-01-01 00:00:00"}]
    feitos = ag.ciclo(db, agora=_local(2026, 10, 30, 5, 0), leitores=_leitores(origem), esperar=_esperar)
    assert feitos[0] == ("saude", "sucesso") and feitos[1] == ("cemiterio", "sem_mudanca")


def test_ag3_pular_sem_mudanca_pode_ser_desligado(db, origem, monkeypatch):
    _amostras(db)
    _zera_agenda(db)
    monkeypatch.setenv("AGENDA_PULAR_SEM_MUDANCA", "false")
    get_settings.cache_clear()
    ag.ciclo(db, agora=_local(2026, 10, 28, 5, 0), leitores=_leitores(origem), esperar=_esperar)
    feitos = ag.ciclo(db, agora=_local(2026, 10, 29, 5, 0), leitores=_leitores(origem), esperar=_esperar)
    assert feitos == [("saude", "sucesso"), ("cemiterio", "sucesso")]
    assert origem["SAUDE"].assinaturas == 0


def test_ag4_falha_tenta_de_novo_avisa_e_desiste_no_limite(db, origem, monkeypatch):
    _amostras(db)
    _zera_agenda(db)
    db.execute(update(Agendamento).where(Agendamento.conector == "cemiterio").values(ativo=False))
    db.commit()
    avisos = []
    monkeypatch.setattr(ag, "alertar", lambda assunto, corpo: avisos.append((assunto, corpo)) or True)
    monkeypatch.setenv("AGENDA_TENTATIVAS", "2")
    monkeypatch.setenv("AGENDA_REESPERA_MIN", "30")
    get_settings.cache_clear()
    origem["SAUDE"].falhar_assinatura = True
    t0 = _local(2026, 10, 28, 5, 0)
    assert ag.ciclo(db, agora=t0, leitores=_leitores(origem), esperar=_esperar) == [("saude", "falha")]
    s = _ag(db, "saude")
    assert s.tentativas == 1 and s.ultima_data is None and s.proxima_tentativa_em is not None
    assert "srv-sintetico" not in (s.ultima_mensagem or "")                      # sem host na mensagem
    assert avisos and "Saúde" in avisos[0][0] and "srv-sintetico" not in avisos[0][1]
    assert ag.ciclo(db, agora=t0 + timedelta(minutes=10), leitores=_leitores(origem), esperar=_esperar) == []
    assert ag.ciclo(db, agora=t0 + timedelta(minutes=31), leitores=_leitores(origem), esperar=_esperar) == [("saude", "falha")]
    s = _ag(db, "saude")
    assert s.ultima_data == date(2026, 10, 28) and s.tentativas == 0 and "Tentativas esgotadas" in s.ultima_mensagem


def test_ag1_conector_sem_amostra_fica_bloqueado_sem_ler_a_origem(db, origem, monkeypatch):
    _zera_agenda(db)
    monkeypatch.setattr(ccomp, "motivo_bloqueio", lambda db, conector: ccomp.SEM_AMOSTRA)
    feitos = ag.ciclo(db, agora=_local(2026, 10, 28, 5, 0), leitores=_leitores(origem), esperar=_esperar)
    assert feitos == [("saude", "bloqueada"), ("cemiterio", "bloqueada")]
    assert origem["SAUDE"].assinaturas == 0 and _ag(db, "saude").ultima_data == date(2026, 10, 28)


def test_ag2_carga_manual_em_andamento_vira_ocupada_e_tenta_depois(db, origem, monkeypatch):
    _amostras(db)
    _zera_agenda(db)

    def ocupado(db, usuario, conector, somente_camadas=False, modo="completa"):
        raise cc.Conflito("ocupado")
    monkeypatch.setattr(ccomp, "iniciar", ocupado)
    feitos = ag.ciclo(db, agora=_local(2026, 10, 28, 5, 0), leitores=_leitores(origem), esperar=_esperar)
    assert feitos[0] == ("saude", "ocupada") and _ag(db, "saude").proxima_tentativa_em is not None


def test_ag6_forcar_roda_fora_da_janela(db, origem, monkeypatch):
    _amostras(db)
    _zera_agenda(db, ativo=False)
    monkeypatch.setenv("AGENDA_JANELA", "00:00-06:00")
    get_settings.cache_clear()
    db.execute(update(Agendamento).where(Agendamento.conector == "cemiterio").values(ativo=True, horario="05:00"))
    db.commit()
    ag.forcar(db, "cemiterio", "admin@aion.local")
    feitos = ag.ciclo(db, agora=_local(2026, 10, 28, 14, 0), leitores=_leitores(origem), esperar=_esperar)
    assert feitos == [("cemiterio", "sucesso")] and _ag(db, "cemiterio").forcar is False


# ---------------------------------------------------------------- API e inicialização

def test_ag1_api_estado_alterar_e_permissoes(client, tokens, db, origem):
    _zera_agenda(db, ativo=False)
    r = client.get("/api/agenda", headers=tokens["leitura"])
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["habilitada"] is False and d["dia_todo"] is True
    assert [i["conector"] for i in d["itens"]] == ["saude", "cemiterio", "iptu", "divida", "financeiro", "sil", "educacao", "contagens"]  # 0034: contagens 05:45
    assert d["itens"][0]["horario"] == "01:00"
    assert client.put("/api/agenda/saude", json={"ativo": True}, headers=tokens["operador"]).status_code == 403
    negado = db.scalar(select(Auditoria).where(Auditoria.acao == "acesso_negado", Auditoria.alvo == "/api/agenda/saude")
                       .order_by(Auditoria.id.desc()))
    assert negado is not None and negado.usuario == "operador@aion.local"
    assert client.put("/api/agenda/saude", json={"horario": "25:00"}, headers=tokens["admin"]).status_code == 422
    assert client.put("/api/agenda/inexistente", json={"ativo": True}, headers=tokens["admin"]).status_code == 404
    r = client.put("/api/agenda/saude", json={"horario": "02:30", "ativo": True}, headers=tokens["admin"])
    assert r.status_code == 200
    item = r.json()["itens"][0]
    assert item["horario"] == "02:30" and item["ativo"] is True and item["proxima_em"] is not None
    aud = db.scalar(select(Auditoria).where(Auditoria.acao == "agenda_alterada").order_by(Auditoria.id.desc()))
    assert aud.detalhe["antes"]["horario"] == "01:00" and aud.detalhe["depois"]["horario"] == "02:30"
    _zera_agenda(db, ativo=False)
    db.execute(update(Agendamento).where(Agendamento.conector == "saude").values(horario="01:00"))
    db.commit()


def test_ag6_executar_agora_so_com_agenda_habilitada(client, tokens, db, monkeypatch):
    _zera_agenda(db, ativo=False)
    assert client.post("/api/agenda/saude/executar-agora", headers=tokens["admin"]).status_code == 409
    monkeypatch.setenv("AGENDA_HABILITADA", "true")
    get_settings.cache_clear()
    assert client.post("/api/agenda/saude/executar-agora", headers=tokens["leitura"]).status_code == 403
    r = client.post("/api/agenda/saude/executar-agora", headers=tokens["admin"])  # agendamento desligado: 409
    assert r.status_code == 409 and "desligado" in r.json()["detail"]
    aud = db.scalar(select(Auditoria).where(Auditoria.acao == "agenda_executar_agora_recusada")
                    .order_by(Auditoria.id.desc()))
    assert aud.alvo == "agendamento:saude" and aud.detalhe["motivo"] == "desligado"
    db.execute(update(Agendamento).where(Agendamento.conector == "saude").values(ativo=True))
    db.commit()
    r = client.post("/api/agenda/saude/executar-agora", headers=tokens["admin"])
    assert r.status_code == 202 and r.json()["itens"][0]["forcar"] is True
    _zera_agenda(db, ativo=False)
    get_settings.cache_clear()


def test_ag1_agendador_nao_sobe_em_teste(monkeypatch):
    from app.main import iniciar_agenda
    monkeypatch.setenv("AGENDA_HABILITADA", "true")
    get_settings.cache_clear()
    assert iniciar_agenda() is None  # APP_ENV=test
    get_settings.cache_clear()


def test_ag4_alerta_por_email_opcional_e_sem_excecao(monkeypatch):
    monkeypatch.delenv("SMTP_HOST", raising=False)
    get_settings.cache_clear()
    assert ag.alertar("x", "y") is False
    enviados = []

    class SMTP:
        def __init__(self, host, port, timeout):
            self.host = host

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def starttls(self):
            pass

        def login(self, u, s):
            pass

        def send_message(self, msg):
            enviados.append(msg)
    monkeypatch.setenv("SMTP_HOST", "smtp.invalid")
    monkeypatch.setenv("ALERTA_PARA", "ti@franca.invalid")
    get_settings.cache_clear()
    monkeypatch.setattr(ag.smtplib, "SMTP", SMTP)
    assert ag.alertar("Carga falhou", "corpo") is True and enviados[0]["Subject"] == "[SIGPMF] Carga falhou"

    class Quebrado(SMTP):
        def send_message(self, msg):
            raise OSError("smtp.invalid recusou")
    monkeypatch.setattr(ag.smtplib, "SMTP", Quebrado)
    assert ag.alertar("x", "y") is False
    get_settings.cache_clear()


def test_ag5_so_um_processo_agenda_e_a_thread_para(banco, monkeypatch):
    import threading
    import time
    monkeypatch.setenv("AGENDA_INTERVALO_S", "5")
    get_settings.cache_clear()
    chamadas = {"a": 0, "b": 0}
    rodando = threading.local()

    def ciclo_falso(db, parar=None, **kw):
        chamadas[rodando.nome] += 1

    monkeypatch.setattr(ag, "ciclo", ciclo_falso)
    a, b = ag.Agendador(), ag.Agendador()

    def alvo(agendador, nome):
        rodando.nome = nome
        agendador._rodar()
    ta = threading.Thread(target=alvo, args=(a, "a"), daemon=True)
    ta.start()
    limite = time.monotonic() + 10
    while chamadas["a"] == 0 and time.monotonic() < limite:
        time.sleep(0.05)
    tb = threading.Thread(target=alvo, args=(b, "b"), daemon=True)
    tb.start()
    time.sleep(1)
    assert chamadas["a"] >= 1 and chamadas["b"] == 0  # o segundo processo fica de reserva (sem a trava de líder)
    a.parar()
    ta.join(timeout=10)
    assert not ta.is_alive()
    limite = time.monotonic() + 10  # o primeiro soltou a trava ao parar: o reserva assume
    while chamadas["b"] == 0 and time.monotonic() < limite:
        time.sleep(0.05)
    b.parar()
    tb.join(timeout=10)
    assert chamadas["b"] >= 1 and not tb.is_alive()
    get_settings.cache_clear()
