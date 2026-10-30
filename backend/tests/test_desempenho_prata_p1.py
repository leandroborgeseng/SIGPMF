"""P1 desempenho prata/ouro: DISTINCT ON == max(id), cancelamento por lote (atômico, sem duplicar), trava do caminho
linear fora de APP_ENV=test, pulso por tempo e migração 0036 idempotente. Só dados sintéticos ZZTESTE."""
import hashlib
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select, text

from app.gold import visao
from app.models.camadas import RegistroBronze
from app.models.controle import Execucao
from app.silver import processamento as pr
from tests import sintetico_prata

from .conftest import alembic_cfg


def _versoes_extras(db, ex_id: int) -> None:
    """Segunda versão de algumas chaves e uma linha em quarentena (a última versão vem do maior id sem quarentena)."""
    for i in (1, 5, 9):
        d = {"Matricula": f"SIN{i:07d}", "Nome": f"ZZTESTE NOVO {i}", "Dt_Nasc": "1980-01-01", "Mae": "ZZTESTE MAE",
             "Cpf": None, "Cartao_Sus": None, "Sexo": "F"}
        db.add(RegistroBronze(sistema_origem="SAUDE", tabela="pacientes", id_origem=d["Matricula"], dados=d,
                              hash=hashlib.sha256(repr(d).encode()).hexdigest(), execucao_id=ex_id,
                              atualizado_origem_em=datetime(2026, 2, 1, tzinfo=UTC)))
    d = {"Matricula": "SIN0000002", "Nome": "ZZTESTE QUARENTENA"}
    db.add(RegistroBronze(sistema_origem="SAUDE", tabela="pacientes", id_origem="SIN0000002", dados=d, hash="q" * 64,
                          execucao_id=ex_id, quarentena="chave_nula"))
    db.flush()


def _ultimas_antigo(db) -> list[int]:
    """Consulta anterior ao P1 (max(id) por concat) como referência."""
    R = RegistroBronze
    so_fatos = pr.sistemas_so_fatos() + ("GESTAOESCOLAR", "CORESSO")
    sub = select(func.max(R.id)).where(R.quarentena.is_(None), R.sistema_origem.not_in(so_fatos),
                                       func.concat(R.sistema_origem, "|", R.tabela).not_in(pr.tabelas_fato())
                                       ).group_by(R.sistema_origem, R.tabela, R.id_origem)
    return list(db.scalars(select(R.id).where(R.id.in_(sub)).order_by(R.id)))


def test_distinct_on_igual_ao_max_id(db):
    try:
        ex_id = sintetico_prata.gera(db, 300)
        _versoes_extras(db, ex_id)
        novo = [r.id for r, _ in pr._ultimas_versoes(db)]
        assert novo == _ultimas_antigo(db) and len(novo) > 300 - 1
    finally:
        db.rollback()


def _gold_snap(db):
    return sorted(tuple(r) for r in db.execute(text(
        "SELECT cidadao_id, dominio, campo, valor, sistema FROM gold.cidadao_dados")))


@pytest.mark.parametrize("camada", ["prata", "ouro"])
def test_cancelamento_por_lote_atomico_e_sem_duplicar(db, camada):
    try:
        sintetico_prata.gera(db, 3 * pr.LOTE + 100)
        pr.reconstruir(db)
        visao.reconstruir(db)
        antes, gantes = sintetico_prata.snapshot(db), _gold_snap(db)
        chamadas = []

        def cancelado():
            chamadas.append(1)
            return len(chamadas) >= 2  # pedido chega durante o 2º lote: para no lote seguinte

        sp = db.begin_nested()
        with pytest.raises(pr.CamadasCanceladas):
            if camada == "prata":
                pr.reconstruir(db, cancelado=cancelado)
            else:
                visao.reconstruir(db, cancelado=cancelado)
        assert len(chamadas) == 2  # parou na 1ª checagem depois do pedido (dentro de 1 lote)
        sp.rollback()  # quem chama faz rollback: nada parcial
        assert sintetico_prata.snapshot(db) == antes and _gold_snap(db) == gantes
        pr.reconstruir(db)  # rodar de novo depois da interrupção: mesmo resultado, sem duplicar
        visao.reconstruir(db)
        assert sintetico_prata.snapshot(db) == antes and _gold_snap(db) == gantes
    finally:
        db.rollback()


def test_carga_cancelada_durante_camadas_mantem_prata(db, monkeypatch):
    from app.services import carga_completa as cc
    ex = Execucao(tipo="carga_completa", sistema="SAUDE", status="em_andamento", usuario="zzteste", cancelar=True)
    db.add(ex)
    db.commit()
    try:
        monkeypatch.setattr(cc, "sinal_de_vida", lambda *a, **k: None)
        monkeypatch.setattr(cc, "_inicio_etapa", lambda db, ex, etapa, total: 0.0)
        monkeypatch.setattr(cc.processamento, "LOTE", 1)
        cc._camadas(db, ex, "saude", "SAUDE", pode_pular=False)
        db.refresh(ex)
        assert ex.status == "cancelada" and "prata/ouro anteriores ficaram" in ex.mensagem
    finally:
        db.rollback()
        db.execute(text("DELETE FROM controle.logs WHERE execucao_id = :i"), {"i": ex.id})
        db.execute(text("DELETE FROM controle.execucoes WHERE id = :i"), {"i": ex.id})
        db.commit()


def test_caminho_linear_bloqueado_fora_de_teste(db, monkeypatch):
    monkeypatch.setenv("APP_ENV", "prod")
    with pytest.raises(RuntimeError, match="APP_ENV=test"):
        pr.reconstruir(db, indexado=False)
    with pytest.raises(RuntimeError, match="APP_ENV=test"):
        visao.reconstruir_legado(db)
    db.rollback()


def test_pulso_por_tempo_no_maximo_30s(monkeypatch):
    agora = [0.0]
    monkeypatch.setattr(pr.time, "monotonic", lambda: agora[0])
    pulsos = []
    p = pr.Pulso(lambda n, t: pulsos.append(agora[0]))
    for n in range(1, 4000):  # menos de um lote, 0,1 s por item: só o tempo dispara o pulso
        agora[0] += 0.1
        p.passo(n, None)
    assert pulsos and pulsos[0] <= 30 and all(b - a <= 30 for a, b in zip(pulsos, pulsos[1:], strict=False))
    assert pr.Pulso.PULSO_S <= 30


def test_migracao_0036_idempotente(banco):
    """Banco próprio (o CONCURRENTLY espera transações abertas de outros testes no banco compartilhado)."""
    from alembic import command
    from sqlalchemy import create_engine
    from sqlalchemy.engine import make_url
    url = make_url(banco)
    nome = f"{url.database}_mig0036"
    adm = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        with adm.connect() as c:
            c.execute(text(f'DROP DATABASE IF EXISTS "{nome}"'))
            c.execute(text(f'CREATE DATABASE "{nome}"'))
    except Exception:  # noqa: BLE001
        adm.dispose()
        pytest.skip("sem permissão para criar o banco de teste da migração")
    alvo = url.set(database=nome).render_as_string(hide_password=False)
    cfg = alembic_cfg()
    cfg.set_main_option("sqlalchemy.url", alvo.replace("%", "%%"))
    try:
        command.upgrade(cfg, "head")
        command.stamp(cfg, "0033")
        command.upgrade(cfg, "head")  # 2ª execução com o índice já criado
        command.downgrade(cfg, "0033")
        command.stamp(cfg, "0036")
        command.downgrade(cfg, "0033")  # 2º downgrade com o índice já removido
        command.upgrade(cfg, "head")
        eng = create_engine(alvo)
        with eng.connect() as c:
            ok = c.scalar(text("SELECT i.indisvalid FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid "
                               "WHERE c.relname = 'ix_registros_ultima_versao'"))
        eng.dispose()
        assert ok is True
    finally:
        with adm.connect() as c:
            c.execute(text(f'DROP DATABASE IF EXISTS "{nome}" WITH (FORCE)'))
        adm.dispose()
