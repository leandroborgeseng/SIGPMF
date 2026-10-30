"""Releitura de uso único no boot (SIGPMF_RELEITURA_FONTE). Só dados sintéticos ZZTESTE (CPFs 999000...)."""
import threading
import time

import pytest
from sqlalchemy import create_engine, func, select, text, update

from app.core.config import get_settings
from app.models.controle import AcaoUnica, Agendamento, Auditoria, Execucao
from app.services import carga_gestaoescolar as cge
from app.services import releitura_deploy as rd
from app.services import trava as tv
from tests.conftest import TEST_URL
from tests.test_zzzzzzzzzzzzzzzzzzz_gestaoescolar import LeitorMock, _ciclo, cfg, cpf_sintetico
from tests.test_zzzzzzzzzzzzzzzzzzz_gestaoescolar import janela_env as janela_env  # noqa: F401 (fixture)
from tests.test_zzzzzzzzzzzzzzzzzzz_gestaoescolar import limpo as limpo  # noqa: F401 (fixture)
from tests.test_zzzzzzzzzzzzzzzzzzzzzzz_pessoadocumento_tabela_vazia import CATALOGO_99, _dados_99

FONTE = "PES_PessoaDocumento"
SHA1 = "0eb93cd" + "a" * 33
SHA2 = "1" * 40


class Fab:
    """Fábrica de leitor como context manager; registra quantas vezes abriu."""

    def __init__(self, leitor):
        self.leitor, self.aberturas = leitor, 0

    def __call__(self):
        fab = self

        class _C:
            def __enter__(self):
                fab.aberturas += 1
                return fab.leitor

            def __exit__(self, *a):
                return False
        return _C()


@pytest.fixture
def base(janela_env, limpo, monkeypatch):  # noqa: F811
    """Educação já carregada com PES_PessoaDocumento vazia (como na produção), agenda da educação LIGADA (RD1:
    desligada não relê)."""
    db = limpo
    janela_env("false")  # boot de dia: o teste não depende do relógio
    db.execute(update(Agendamento).where(Agendamento.conector == "educacao").values(ativo=True, forcar=False))
    db.execute(text("DELETE FROM controle.acoes_unicas"))
    db.commit()
    d = _dados_99()
    _ciclo(db, LeitorMock({**d, FONTE: []}, CATALOGO_99), cfg(max_lotes=100))
    monkeypatch.setenv("SIGPMF_RELEITURA_FONTE", FONTE)
    for v in rd.VARS_SHA:
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("SOURCE_COMMIT", SHA1)
    get_settings.cache_clear()
    yield db, d
    get_settings.cache_clear()


def contagens(db):
    from app.models.camadas import RegistroBronze
    return dict(db.execute(select(RegistroBronze.tabela, func.count()).where(
        RegistroBronze.sistema_origem.in_(("GESTAOESCOLAR", "CORESSO"))).group_by(RegistroBronze.tabela)).all())


def boot(d, leitor=None):
    lm = leitor or LeitorMock(d, CATALOGO_99)
    fab = Fab(lm)
    return rd.no_boot(saude=lambda: True, leitor_factory=fab, cfg=cfg(max_lotes=100)), lm, fab


def n_exec(db, tipo):
    return db.scalar(select(func.count()).select_from(Execucao).where(Execucao.tipo == tipo))


def test_sem_variavel_nada_acontece(base, monkeypatch):
    db, d = base
    monkeypatch.delenv("SIGPMF_RELEITURA_FONTE")
    get_settings.cache_clear()
    antes = (n_exec(db, cge.TIPO_COMPLETA), n_exec(db, rd.TIPO), contagens(db))
    assert rd.iniciar_em_fundo() is None
    assert rd.no_boot(saude=lambda: pytest.fail("não deveria checar saúde")) is None
    assert (n_exec(db, cge.TIPO_COMPLETA), n_exec(db, rd.TIPO), contagens(db)) == antes
    assert db.scalar(select(func.count()).select_from(AcaoUnica)) == 0


def test_boot_nao_espera_e_health_ok_durante_releitura(base, client, monkeypatch):
    liberar, comecou = threading.Event(), threading.Event()

    def lento():
        comecou.set()
        liberar.wait(10)
    monkeypatch.setattr(rd, "no_boot", lento)
    t0 = time.monotonic()
    t = rd.iniciar_em_fundo()
    assert time.monotonic() - t0 < 1.0 and t is not None and t.daemon
    assert comecou.wait(5) and t.is_alive()
    assert client.get("/health").status_code == 200  # saudável enquanto a releitura ainda roda
    liberar.set()
    t.join(5)


def test_espera_saude_antes_de_ler(base):
    db, d = base
    lm = LeitorMock(d, CATALOGO_99)
    r = rd.no_boot(saude=lambda: False, leitor_factory=Fab(lm), cfg=cfg())
    assert r["codigo"] == "sem_saude" and lm.sqls == []


def test_roda_uma_vez_auditoria_prata_ouro_e_outras_12_intactas(base, client, tokens):
    from app.models.camadas import EscolaAluno
    db, d = base
    antes = contagens(db)
    r, lm, fab = boot(d)
    assert r["resultado"] == "concluida", r
    principais = {s.split(" FROM ", 1)[1].split(" AS ")[0] for s in lm.sqls}
    assert principais == {"[FRA_CoreSSO].[dbo].[PES_PessoaDocumento]"}  # só a fonte pedida foi lida
    depois = contagens(db)
    assert {t: n for t, n in depois.items() if t != FONTE} == {t: n for t, n in antes.items() if t != FONTE}
    assert depois[FONTE] > 0
    db.expire_all()
    ex = db.get(Execucao, r["execucao_id"])
    assert ex.status == "sucesso" and ex.usuario == rd.USUARIO and ex.tipo == cge.TIPO_COMPLETA
    assert {a.alu_id: a for a in db.scalars(select(EscolaAluno))}[1].cpf == cpf_sintetico(1)  # prata/ouro reprocessados
    aud = db.scalar(select(Auditoria).where(Auditoria.acao == "educacao_releitura_fonte").order_by(Auditoria.id.desc()))
    assert aud.usuario == "sistema-deploy"
    fim = db.scalar(select(Auditoria).where(Auditoria.acao == "educacao_releitura_deploy").order_by(Auditoria.id.desc()))
    assert fim.usuario == "sistema-deploy" and cpf_sintetico(1) not in str(fim.detalhe)
    m = db.scalar(select(AcaoUnica).where(AcaoUnica.fonte == FONTE))
    assert m.status == "concluida" and m.token == SHA1 and m.execucao_id == ex.id
    st = client.get("/api/educacao/releitura-deploy", headers=tokens["admin"]).json()
    assert st[0]["status"] == "concluida" and st[0]["fonte"] == FONTE
    assert client.get("/api/educacao/releitura-deploy", headers=tokens["leitura"]).status_code == 403


def test_mesmo_sha_nao_repete_sha_novo_repete(base, monkeypatch):
    db, d = base
    assert rd.sha_deploy() == SHA1
    assert boot(d)[0]["resultado"] == "concluida"
    n = n_exec(db, cge.TIPO_COMPLETA)
    for _ in range(2):  # restarts no mesmo SHA
        r, lm, fab = boot(d)
        assert r["resultado"] == "ja_executada" and lm.sqls == [] and fab.aberturas == 0
    assert n_exec(db, cge.TIPO_COMPLETA) == n
    monkeypatch.setenv("SOURCE_COMMIT", SHA2)  # deploy novo
    r3, lm3, _ = boot(d)
    assert r3["resultado"] == "concluida" and lm3.sqls
    assert {m.token: m.status for m in db.scalars(select(AcaoUnica))} == {SHA1: "concluida", SHA2: "concluida"}


def test_sha_fallbacks_e_invalido(monkeypatch):
    for v in rd.VARS_SHA:
        monkeypatch.delenv(v, raising=False)
    assert rd.sha_deploy() is None
    monkeypatch.setenv("SOURCE_COMMIT", "")  # vazio = ausente
    assert rd.sha_deploy() is None
    monkeypatch.setenv("SIGPMF_BUILD_SHA", "ABCDEF1")
    assert rd.sha_deploy() == "abcdef1"
    monkeypatch.setenv("GIT_SHA", SHA2)
    assert rd.sha_deploy() == SHA2
    monkeypatch.setenv("SOURCE_COMMIT", "nao-e-sha;rm")  # inválido: cai no próximo
    assert rd.sha_deploy() == SHA2


def test_sem_sha_recusa_sem_ler(base, client, tokens, monkeypatch):
    db, d = base
    monkeypatch.delenv("SOURCE_COMMIT")
    antes = contagens(db)
    r, lm, fab = boot(d)
    _confere_recusa(db, r, "sem_sha_deploy", antes, lm)
    assert fab.aberturas == 0 and db.scalar(select(func.count()).select_from(AcaoUnica)) == 0
    ult = client.get("/api/dashboard", headers=tokens["leitura"]).json()["ultima_execucao"]
    assert ult["alerta"]["codigo"] == "sem_sha_deploy"


def _confere_recusa(db, r, codigo, antes, lm, client=None):
    assert r["resultado"] == "recusada" and r["codigo"] == codigo
    assert lm.sqls == [] and contagens(db) == antes  # nada lido, bronze igual
    db.expire_all()
    ex = db.get(Execucao, r["execucao_id"])
    assert ex.tipo == rd.TIPO and ex.status == "recusada" and ex.erro_classe == codigo
    assert ex.alerta_publico() == {"codigo": codigo, "texto": rd.TEXTOS[codigo]}
    aud = db.scalar(select(Auditoria).where(Auditoria.acao == "educacao_releitura_deploy_recusada")
                    .order_by(Auditoria.id.desc()))
    assert aud.usuario == "sistema-deploy" and aud.detalhe["codigo"] == codigo
    m = db.scalar(select(AcaoUnica).where(AcaoUnica.fonte == FONTE))
    if m is not None:
        assert m.status != "concluida"


def test_rd1_fonte_desligada_nao_cria_releitura(base):
    """RD1 (caso 348): com ativo=false o deploy não cria releitura: nenhuma execução, nenhum marcador, nada lido."""
    db, d = base
    db.execute(update(Agendamento).where(Agendamento.conector == "educacao").values(ativo=False))
    db.commit()
    try:
        antes = contagens(db)
        n_ex = db.scalar(select(func.count()).select_from(Execucao))
        r, lm, _ = boot(d)
        assert r == {"resultado": "pulada", "codigo": "fonte_desligada", "execucao_id": None}
        assert lm.sqls == [] and contagens(db) == antes
        db.expire_all()
        assert db.scalar(select(func.count()).select_from(Execucao)) == n_ex
        assert db.scalar(select(AcaoUnica).where(AcaoUnica.fonte == FONTE)) is None
        aud = db.scalar(select(Auditoria).where(Auditoria.acao == "educacao_releitura_deploy_pulada")
                        .order_by(Auditoria.id.desc()))
        assert aud.usuario == "sistema-deploy" and aud.detalhe == {"codigo": "fonte_desligada"}
    finally:
        db.execute(update(Agendamento).where(Agendamento.conector == "educacao").values(ativo=True))
        db.commit()

def test_recusa_carga_em_andamento(base):
    db, d = base
    ex = Execucao(tipo=cge.TIPO_COMPLETA, sistema=cge.SISTEMA, status="em_andamento", usuario="zz@aion.local")
    db.add(ex)
    db.commit()
    try:
        antes = contagens(db)
        r, lm, _ = boot(d)
        _confere_recusa(db, r, "em_andamento", antes, lm)
    finally:
        db.execute(update(Execucao).where(Execucao.id == ex.id).values(status="falha"))
        db.commit()


def test_recusa_trava_ocupada(base):
    db, d = base
    eng = create_engine(TEST_URL)
    c = eng.connect()
    assert c.execute(text("select pg_try_advisory_lock(:k)"), {"k": tv.chave_lock(cge.SISTEMA)}).scalar()
    c.commit()
    try:
        antes = contagens(db)
        r, lm, _ = boot(d)
        _confere_recusa(db, r, "trava_ocupada", antes, lm)
    finally:
        c.execute(text("select pg_advisory_unlock(:k)"), {"k": tv.chave_lock(cge.SISTEMA)})
        c.close()
        eng.dispose()
    r2, _, _ = boot(d)  # trava livre: o próximo boot roda (recusa não marcou concluída)
    assert r2["resultado"] == "concluida"


def test_fonte_invalida_recusada_sem_ler(base, monkeypatch):
    db, d = base
    monkeypatch.setenv("SIGPMF_RELEITURA_FONTE", "ZZ_Inexistente")
    get_settings.cache_clear()
    antes = contagens(db)
    r, lm, fab = boot(d)
    _confere_recusa(db, r, "fonte_desconhecida", antes, lm)
    assert fab.aberturas == 0


def test_falha_no_meio_nao_marca_concluida(base):
    db, d = base

    class Quebra(LeitorMock):
        def ler(self, fonte, ultima, limite):
            if fonte.nome == FONTE and ultima is not None:
                raise ConnectionError("zz queda simulada")
            return super().ler(fonte, ultima, limite)
    lm = Quebra(d, CATALOGO_99)
    c = cfg(lote=2, max_lotes=100)
    r = rd.no_boot(saude=lambda: True, leitor_factory=Fab(lm), cfg=c)
    assert r["resultado"] == "falha"
    m = db.scalar(select(AcaoUnica).where(AcaoUnica.fonte == FONTE))
    db.refresh(m)
    assert m.status == "falha" and m.status != "concluida"
    ex = db.get(Execucao, r["execucao_id"])
    assert ex.status == "falha" and "zz queda" not in (ex.mensagem or "")
    r2, _, _ = boot(d)  # mesmo SHA: tenta de novo porque não concluiu
    assert r2["resultado"] == "concluida"



def test_r2_marcador_conferido_de_novo_depois_da_trava(base, monkeypatch):
    """R2: outra instância no mesmo SHA conclui entre a 1ª conferência e a trava -> esta não relê."""
    db, d = base
    real = cge._trava
    eng = create_engine(TEST_URL)

    def trava_depois_da_outra_instancia(sess):
        with eng.begin() as c:  # a "outra instância" termina e marca concluida (fonte + SHA) antes de soltarmos a trava
            c.execute(text("UPDATE controle.acoes_unicas SET status='concluida' WHERE acao=:a AND fonte=:f AND token=:t"),
                      {"a": rd.ACAO, "f": FONTE, "t": SHA1})
        return real(sess)

    monkeypatch.setattr(cge, "_trava", trava_depois_da_outra_instancia)
    n = n_exec(db, cge.TIPO_COMPLETA)
    antes = contagens(db)
    try:
        r, lm, fab = boot(d)
    finally:
        eng.dispose()
    assert r["resultado"] == "ja_executada" and r["codigo"] == "ja_executada"
    assert lm.sqls == [] and fab.aberturas == 0  # nada lido
    assert n_exec(db, cge.TIPO_COMPLETA) == n and contagens(db) == antes
    db.expire_all()
    assert db.scalar(select(AcaoUnica.status).where(AcaoUnica.fonte == FONTE, AcaoUnica.token == SHA1)) == "concluida"
    e2 = create_engine(TEST_URL)  # a trava foi solta
    with e2.connect() as c:
        assert c.execute(text("select pg_try_advisory_lock(:k)"), {"k": tv.chave_lock(cge.SISTEMA)}).scalar()
        c.execute(text("select pg_advisory_unlock(:k)"), {"k": tv.chave_lock(cge.SISTEMA)})
    e2.dispose()


class _Resp:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.mark.parametrize("porta,esperada", [(None, 8000), ("8123", 8123)])
def test_r3_porta_do_health_por_variavel(monkeypatch, porta, esperada):
    """R3: a porta do /health vem de SIGPMF_RELEITURA_SAUDE_PORTA (padrão 8000)."""
    if porta is None:
        monkeypatch.delenv("SIGPMF_RELEITURA_SAUDE_PORTA", raising=False)
    else:
        monkeypatch.setenv("SIGPMF_RELEITURA_SAUDE_PORTA", porta)
    get_settings.cache_clear()
    urls = []

    def falso(url, timeout=None):
        urls.append(url)
        return _Resp()

    monkeypatch.setattr(rd.urllib.request, "urlopen", falso)
    try:
        assert rd.url_saude() == f"http://127.0.0.1:{esperada}/health"
        assert rd.aguarda_saude(max_s=5, passo=0) is True
        assert urls == [f"http://127.0.0.1:{esperada}/health"]
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()


def test_r3_porta_invalida_e_recusada(monkeypatch):
    from pydantic import ValidationError
    monkeypatch.setenv("SIGPMF_RELEITURA_SAUDE_PORTA", "70000")
    get_settings.cache_clear()
    try:
        with pytest.raises(ValidationError):
            get_settings()
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()
