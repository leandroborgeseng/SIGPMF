# Roda por último: grava bronze sintética (ZZTESTE / fixtures) e commita. Parte 2: prata incremental.
"""Parte 2 (PI1, PI5, PI7/PI8, PI12, D6, D6a, D7): bronze.ultima_versao, decisão do modo, equivalência
incremental x recomposição (contagem + md5 da prata e do ouro recomposto), cancelamento/retomada, homologação nunca
vira cidadão. Só dados sintéticos."""
import hashlib
from datetime import UTC, datetime

import pytest
from sqlalchemy import delete, func, select, text

from app.bronze import ingestao
from app.gold import visao
from app.ingestion import fixture_saude_cemiterio, fixture_tributario
from app.ingestion.mapeamentos import MAPEAMENTOS
from app.models.camadas import Cidadao, RegistroBronze, UltimaVersao, Vinculo
from app.models.controle import CamadasMarca, CamadasPendencia, Execucao
from app.services import camadas_incremental as ci
from app.services import ultima_versao as uv
from app.silver import processamento as pr

IGNORA_COLS = {"id", "criado_em", "atualizado_em", "reconstruido_em"}


def _ex(db):
    ex = Execucao(tipo="sintetico_parte2", sistema="SAUDE", status="sucesso", usuario="teste")
    db.add(ex)
    db.commit()
    return ex.id


def _carrega(db, dados: dict, ex_id: int):
    for (s, t), regs in dados.items():
        m = MAPEAMENTOS.get((s, t))
        if m is None:
            continue
        ingestao.ingere(db, s, t, m["chave"], m.get("campo_incremental"), regs, ex_id)
    db.commit()


def _fixtures():
    saude = fixture_saude_cemiterio.gerar()
    cpf = saude["saude"][("SAUDE", "pacientes")][0]["Cpf"]
    d = {}
    for v in saude.values():
        d.update(v)
    d.update(fixture_tributario.gerar(cpf))
    d.update(fixture_tributario.gerar_saude_fatos())
    return d


def snapshot(db) -> dict:
    """Contagem + md5 de cada tabela da prata e do ouro (sem id serial nem carimbos de tempo)."""
    out = {}
    tabs = db.execute(text("SELECT table_schema, table_name FROM information_schema.tables WHERE table_schema IN "
                           "('silver', 'gold') AND table_type = 'BASE TABLE' ORDER BY 1, 2")).all()
    for s, t in tabs:
        cols = [c for (c,) in db.execute(text(
            "SELECT column_name FROM information_schema.columns WHERE table_schema = :s AND table_name = :t "
            "ORDER BY ordinal_position"), {"s": s, "t": t}) if c not in IGNORA_COLS]
        lista = ", ".join(f'"{c}"' for c in cols)
        n, h = db.execute(text(f"SELECT count(*), md5(coalesce(string_agg(x, '|' ORDER BY x), '')) FROM "
                               f"(SELECT ROW({lista})::text AS x FROM {s}.{t}) q")).one()
        out[f"{s}.{t}"] = (int(n), h)
    return out


def rodada(db, recompor: bool = False, cancelado=None) -> str:
    """O mesmo miolo do carga_completa._camadas, sem a execução."""
    ate = uv.sincroniza(db)
    modo, _ = ci.decide(db, ate, recompor)
    if modo == "recomposicao":
        pr.reconstruir(db)
        visao.reconstruir(db)
        ci.marca_tudo(db, ate, None)
        db.commit()
    else:
        ci.executa(db, ate, cancelado=cancelado)
    return modo


@pytest.fixture
def base(db):
    ex_id = _ex(db)
    _carrega(db, _fixtures(), ex_id)
    assert rodada(db) == "recomposicao"
    yield ex_id
    db.rollback()
    db.execute(delete(CamadasPendencia))
    db.execute(delete(CamadasMarca))
    db.commit()


def _novo_trib_e_fatos(db, ex_id, rodada_n: int):
    """Só fontes de fato/tributário mudam: o modo tem que ser incremental."""
    f = _fixtures()
    d = {k: f[k] for k in (("IPTU", "Cadastro"), ("FINANCEIRO", "Financeiro_Exercicio"), ("SAUDE", "Consultas"))}
    d[("IPTU", "Cadastro")] = [dict(x, Iptu_Terr_Liqu=100.0 + rodada_n) for x in d[("IPTU", "Cadastro")]]
    d[("FINANCEIRO", "Financeiro_Exercicio")] = [dict(x, Valor_Pago=float(rodada_n)) for x in
                                                 d[("FINANCEIRO", "Financeiro_Exercicio")]]
    novas = [dict(x, Cons_Paciente=100 + rodada_n) for x in d[("SAUDE", "Consultas")][:1]]
    d[("SAUDE", "Consultas")] = d[("SAUDE", "Consultas")] + novas
    _carrega(db, d, ex_id)


def test_pi5_incremental_igual_a_recomposicao(db, base):
    for n in (1, 2, 3):
        _novo_trib_e_fatos(db, base, n)
        assert rodada(db) == "incremental"
    assert not db.scalar(select(func.count()).select_from(CamadasPendencia))
    inc = snapshot(db)
    assert rodada(db, recompor=True) == "recomposicao"
    assert snapshot(db) == inc  # prata + ouro recomposto: mesma contagem e md5


def test_pi1_sem_mudanca_nao_recalcula(db, base):
    _carrega(db, _fixtures(), base)  # mesmo conteúdo: hash igual, nada novo na bronze
    ate = uv.sincroniza(db)
    assert ci.decide(db, ate) == ("incremental", "só fatos/tributário mudaram")
    res = ci.executa(db, ate)
    assert res["novas"] == res["alteradas"] == res["lotes_chaves"] == 0 and res["ouro"] is None


def test_fonte_de_pessoas_forca_recomposicao(db, base):
    f = _fixtures()
    pac = [dict(f[("SAUDE", "pacientes")][0], Nome="ZZTESTE NOME NOVO")]
    _carrega(db, {("SAUDE", "pacientes"): pac}, base)
    modo, motivo = ci.decide(db, uv.sincroniza(db))
    assert modo == "recomposicao" and "pessoas" in motivo


def test_pi7_pi8_cancelar_e_retomar(db, base, monkeypatch):
    monkeypatch.setattr(ci, "LOTE", 1)
    _novo_trib_e_fatos(db, base, 7)
    chamadas = []
    with pytest.raises(ci.Cancelado):
        rodada(db, cancelado=lambda: (chamadas.append(1), len(chamadas) > 3)[1])
    db.rollback()
    assert db.scalar(select(func.count()).select_from(CamadasPendencia))  # fila durável: o resto ficou
    assert rodada(db) == "incremental"  # retomada continua da fila
    assert not db.scalar(select(func.count()).select_from(CamadasPendencia))
    inc = snapshot(db)
    rodada(db, recompor=True)
    assert snapshot(db) == inc


def _bronze(db, ex_id, io, dados, quarentena=None, ambiente="producao"):
    r = RegistroBronze(sistema_origem="SAUDE", tabela="pacientes", id_origem=io, dados=dados, execucao_id=ex_id,
                       hash=hashlib.sha256(repr((dados, quarentena, ambiente)).encode()).hexdigest(),
                       quarentena=quarentena, ambiente_origem=ambiente,
                       atualizado_origem_em=datetime(2026, 1, 1, tzinfo=UTC))
    db.add(r)
    db.flush()
    return r.id


def test_d6a_versao_valida_seguida_de_quarentena_ou_homologacao(db, base):
    io = "ZZT0000001"
    d = {"Matricula": io, "Nome": "ZZTESTE VALIDO", "Dt_Nasc": "1980-01-01", "Mae": "ZZTESTE MAE", "Sexo": "F"}
    valido = _bronze(db, base, io, d)
    _bronze(db, base, io, {"Matricula": io, "Nome": "ZZTESTE QUARENTENA"}, quarentena="chave_nula")
    _bronze(db, base, io, dict(d, Nome="ZZTESTE HOMOLOGACAO"), ambiente="sandbox")
    db.commit()
    uv.sincroniza(db)
    assert db.get(UltimaVersao, ("SAUDE", "pacientes", io)).registro_id == valido
    ids = [r.id for r, _ in pr._ultimas_versoes(db) if r.id_origem == io]
    assert ids == [valido]
    rodada(db)
    v = db.scalar(select(Vinculo).where(Vinculo.sistema == "SAUDE", Vinculo.chave_origem == io))
    assert v is not None and db.get(Cidadao, v.cidadao_id).nome == "ZZTESTE VALIDO"  # o cidadão não some


def test_d6_homologacao_nunca_vira_cidadao(db, base):
    io = "ZZT0000002"
    _bronze(db, base, io, {"Matricula": io, "Nome": "ZZTESTE SO HOMOLOGACAO", "Dt_Nasc": "1981-01-01",
                           "Mae": "ZZTESTE MAE", "Sexo": "M"}, ambiente="sandbox")
    db.commit()
    uv.sincroniza(db)
    assert db.get(UltimaVersao, ("SAUDE", "pacientes", io)) is None
    rodada(db, recompor=True)
    assert db.scalar(select(func.count()).select_from(Vinculo).where(Vinculo.chave_origem == io)) == 0
    assert not db.scalar(select(func.count()).select_from(Cidadao).where(Cidadao.nome == "ZZTESTE SO HOMOLOGACAO"))


def test_atualiza_na_carga_ignora_homologacao_e_id_menor(db, base):
    uv.atualiza(db, "SAUDE", "zz_t", [("a", 10), ("a", 5)])
    uv.atualiza(db, "SAUDE", "zz_t", [("a", 7)])
    uv.atualiza(db, "SAUDE", "zz_t", [("b", 9)], ambiente="sandbox")
    db.flush()
    assert db.get(UltimaVersao, ("SAUDE", "zz_t", "a")).registro_id == 10
    assert db.get(UltimaVersao, ("SAUDE", "zz_t", "b")) is None
    db.rollback()


def _zera_uv(db):
    db.execute(delete(UltimaVersao))
    db.execute(delete(CamadasMarca).where(CamadasMarca.sistema == uv.MARCA[0]))
    db.commit()


def test_sincroniza_idempotente_e_em_lotes(db, base):
    _zera_uv(db)
    uv.sincroniza(db)  # um lote só
    antes = sorted(db.execute(select(UltimaVersao.sistema_origem, UltimaVersao.tabela, UltimaVersao.id_origem,
                                     UltimaVersao.registro_id)).all())
    _zera_uv(db)
    uv.sincroniza(db, lote=7)
    uv.sincroniza(db, lote=7)
    depois = sorted(db.execute(select(UltimaVersao.sistema_origem, UltimaVersao.tabela, UltimaVersao.id_origem,
                                      UltimaVersao.registro_id)).all())
    assert depois == antes


def test_d4_hash_conteudo_preenchido_na_carga_nova(db, base):
    regs = [{"Matricula": "ZZT0000003", "Nome": "ZZTESTE HASH", "dt_ultimaatual": "2026-01-01 00:00:00"}]
    ingestao.ingere(db, "SAUDE", "pacientes", "Matricula", "dt_ultimaatual", regs, base)
    db.commit()
    r = db.scalar(select(RegistroBronze).where(RegistroBronze.id_origem == "ZZT0000003"))
    assert r.hash_conteudo == ingestao.hash_registro(regs[0]) == r.hash  # sem rowversion: o mesmo do hash atual
