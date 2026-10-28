"""Ouro em streaming (27/10) x algoritmo antigo: mesmo resultado em gold.cidadao_dados e gold.cidadao_sistemas.
Dados sintéticos ZZTESTE, dentro de uma transação desfeita no fim (não muda as contagens dos outros testes)."""
import random
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.gold import visao
from app.models.camadas import Cidadao, CidadaoSistemas, DadoGold, RegistroBronze, SistemaSilver, Vinculo
from app.models.controle import Execucao


def _snapshot(db):
    db.flush()
    db.expire_all()
    dados = sorted((d.cidadao_id, d.dominio, d.campo, d.valor, d.sistema, d.fonte_atualizado_em, d.mais_recente)
                   for d in db.scalars(select(DadoGold)))
    sis = sorted((c.cidadao_id, c.nome, tuple(c.sistemas), tuple(c.dominios), c.atualizado_em)
                 for c in db.scalars(select(CidadaoSistemas)))
    return dados, sis


def _popula(db, n=400, semente=5):
    rnd = random.Random(semente)
    ex = Execucao(tipo="zzteste_ouro", sistema="SAUDE", status="sucesso", usuario="zzteste")
    db.add(ex)
    db.flush()
    for cod, dom in (("SAUDE", "saude"), ("CEMITERIO", "cemiterio"), ("ZZSEMDOM", None)):
        if db.get(SistemaSilver, cod) is None:
            db.add(SistemaSilver(codigo=cod, nome=f"ZZTESTE {cod}", dominio=dom))
    base = datetime(2026, 1, 1, tzinfo=UTC)
    cids = [f"zzteste-{i:05d}" for i in range(n)]
    for i, cid in enumerate(cids):
        db.add(Cidadao(cidadao_id=cid, nome=f"ZZTESTE PESSOA {i}", atualizado_em=base if i % 3 else None,
                       falecido=i % 17 == 0, obito_fonte="CEMITERIO.Obitos" if i % 34 == 0 else None))
    db.flush()
    k = 0
    for cid in cids[: n - 7]:  # os 7 últimos ficam sem vínculo (cidadao_sistemas vazio; falecido sem vínculo)
        for _ in range(rnd.randint(1, 4)):
            k += 1
            sis, tab, dados = rnd.choice([
                ("SAUDE", "pacientes", {"Matricula": f"ZZ{k}", "Nome": f"ZZTESTE N{rnd.randint(0, 3)}",
                                        "Dt_Nasc": rnd.choice(["1980-01-02", "02/01/1980", None]),
                                        "Mae": rnd.choice(["ZZTESTE MAE", "", None]), "Sexo": rnd.choice("MF"),
                                        "Complemento": rnd.choice(["RUA ZZTESTE", None]), "Nr": "1", "Cep": None,
                                        "Cpf": "00000000191", "Cartao_Sus": None}),
                ("CEMITERIO", "Titulares", {"Cod_Cemiterio": 1, "Quadra": k, "Sepultura": 1, "Sequencia": 1,
                                            "Titular": f"ZZTESTE T{rnd.randint(0, 3)}", "CPF": None,
                                            "Endereco": rnd.choice(["AV ZZTESTE", None]), "Inclusao": None}),
                ("ZZSEMDOM", "zz", {"x": k})])
            fonte = rnd.choice([None, base, base + timedelta(days=rnd.randint(-3, 3))])
            r = RegistroBronze(sistema_origem=sis, tabela=tab, id_origem=f"ZZ{k}", dados=dados, hash=f"zz{k}",
                               execucao_id=ex.id, atualizado_origem_em=fonte)
            db.add(r)
            db.flush()
            db.add(Vinculo(cidadao_id=cid, sistema=sis, tabela=tab, chave_origem=f"ZZ{k}", bronze_id=r.id,
                           metodo="cpf", confianca=1.0, atributos=[], fonte_atualizado_em=fonte))
    db.flush()


def test_ouro_streaming_igual_ao_legado(db):
    try:
        _popula(db)
        visao.reconstruir_legado(db)
        antes = _snapshot(db)
        chamadas = []
        visao.reconstruir(db, progresso=lambda n, t: chamadas.append((n, t)))
        depois = _snapshot(db)
        assert antes[0] and antes[1]
        assert depois == antes
        assert chamadas and chamadas[-1][0] == chamadas[-1][1]  # progresso final = total de vínculos
    finally:
        db.rollback()


def test_ouro_streaming_em_lotes_pequenos(db, monkeypatch):
    """Vários lotes de INSERT e vários sinais de vida (GOLD_LOTE pequeno) sem mudar o resultado."""
    try:
        _popula(db, n=120, semente=9)
        visao.reconstruir_legado(db)
        antes = _snapshot(db)
        monkeypatch.setattr(visao, "GOLD_LOTE", 7)
        chamadas = []
        visao.reconstruir(db, progresso=lambda n, t: chamadas.append(n))
        assert _snapshot(db) == antes
        assert len(chamadas) > 5
    finally:
        db.rollback()
