"""Dívida em aberto (DP-P20/DP-P4/DP-P19/DP-P1): saldo líquido por linha do débito, só COMPO D/G/P/B.

Fixture SINTÉTICA ZZTESTE gravada na bronze DIVIDA (Divida + Divida_Conta_Corrente); nenhum valor real.
"""
import re
from decimal import Decimal

import pytest
from sqlalchemy import func, select, text

from app.models.camadas import DividaAtiva, RegistroBronze
from app.silver import tributario
from app.silver.tributario import COMPO_ABERTO, situacao_divida

D = Decimal


@pytest.mark.parametrize("compo,bruto,pago,extinta,esperado", [
    ("D", D("100"), D("30"), False, (D("70"), True, None)),
    ("G", D("100"), None, False, (D("100"), True, None)),
    ("P", D("50"), D("80"), False, (D("0"), False, None)),       # piso 0: pago maior que o total
    ("B", D("10"), D("10"), False, (D("0"), False, None)),
    ("D", D("100"), None, True, (D("100"), False, None)),        # extinta fica fora
    ("Q", D("90"), None, False, (D("90"), False, "quitado_sem_baixa")),
    ("Q", D("90"), D("90"), False, (D("0"), False, None)),
    ("N", D("500"), None, False, (D("500"), False, None)),       # mestre de consolidação
    ("R", D("40"), None, False, (D("40"), False, None)),         # aguarda decisão do Leandro
    (None, D("40"), None, False, (D("40"), False, None)),        # sem COMPO não entra
    ("d", D("5"), None, False, (D("5"), True, None)),
])
def test_situacao_divida(compo, bruto, pago, extinta, esperado):
    _c, liquido, aberto, qualidade = situacao_divida(compo, bruto, pago, extinta)
    assert (liquido, aberto, qualidade) == esperado


def test_compo_aberto_travado():
    assert COMPO_ABERTO == {"D", "G", "P", "B"}
    for fora in "RSXTAC" + "EHKNQZMW":
        assert fora not in COMPO_ABERTO


def _bronze(db, ex_id, tabela, linhas):
    for i, d in enumerate(linhas):
        db.add(RegistroBronze(sistema_origem="DIVIDA", tabela=tabela, ambiente_origem="producao", execucao_id=ex_id,
                              id_origem=f"zzteste-{tabela}-{i}", dados=d, hash=f"zz{tabela}{i}"))


def _div(codi, sequ, ano, valor, compo, extinta=False):
    return {"CODI": codi, "SEQU": sequ, "ANO": ano, "Nr_Carne": 1, "Cod_Nat": 3, "DT_INSCR": "2025-03-01 00:00:00",
            "VR_ORIGEM": valor, "VR_TOTA_DIVI": valor, "COMPO": compo, "QTDE_PROTESTO": 0,
            "Data_Extincao": "2025-01-10 00:00:00" if extinta else None, "DT_CALCULO": "2026-10-01 00:00:00"}


def _pag(codi, sequ, ano, seq, valor):
    return {"Data_Movimentacao": "2026-05-02 00:00:00", "Sequencia": seq, "CODI": codi, "SEQU": sequ, "ANO": ano,
            "Vr_Pago": valor, "Compo": "P"}


@pytest.fixture
def prata(db):
    assert "_test" in db.bind.url.database
    db.execute(text("DELETE FROM silver.dividas"))
    db.execute(text("DELETE FROM bronze.registros WHERE sistema_origem = 'DIVIDA'"))
    cod = "ZZTESTE0000001"
    linhas = [_div(cod, "00", 2020, 500.0, "N")]                                 # original consolidado (mestre N): fora
    linhas += [_div(cod, f"{i:02d}", 2021, 50.0, "P") for i in range(1, 11)]     # 10 parcelas de 50
    linhas += [_div("ZZTESTE0000002", "00", 2022, 200.0, "D"),                   # D com pagamento parcial 80 -> 120
               _div("ZZTESTE0000003", "00", 2022, 30.0, "G"),                    # membro reaberto de mestre cancelado
               _div("ZZTESTE0000004", "00", 2022, 25.0, "B"),                    # B pago a mais -> piso 0
               _div("ZZTESTE0000005", "00", 2022, 70.0, "Q"),                    # Q sem baixa -> qualidade
               _div("ZZTESTE0000006", "00", 2022, 60.0, "R"),
               _div("ZZTESTE0000007", "00", 2022, 99.0, "D", extinta=True)]
    pagos = [_pag(cod, f"{i:02d}", 2021, 1, 50.0) for i in (1, 2, 3)]           # 3 parcelas pagas
    pagos += [_pag("ZZTESTE0000002", "00", 2022, 1, 50.0), _pag("ZZTESTE0000002", "00", 2022, 2, 30.0),
              _pag("ZZTESTE0000004", "00", 2022, 1, 40.0), _pag("ZZTESTE0000006", "00", 2022, 1, 0.0)]
    from app.models.controle import Execucao
    ex = Execucao(tipo="zzteste", sistema="DIVIDA", status="sucesso")
    db.add(ex)
    db.flush()
    _bronze(db, ex.id, "Divida", linhas)
    _bronze(db, ex.id, "Divida_Conta_Corrente", pagos)
    db.flush()
    tributario.reconstruir(db)
    db.flush()
    db.commit()
    yield db
    db.rollback()
    db.execute(text("DELETE FROM silver.dividas"))
    db.execute(text("DELETE FROM bronze.registros WHERE id_origem LIKE 'zzteste-%'"))
    db.execute(text("DELETE FROM controle.execucoes WHERE tipo = 'zzteste'"))
    db.commit()


def test_saldo_liquido_por_linha_e_parcelamento(prata):
    db = prata
    soma = lambda *w: float(db.scalar(select(func.coalesce(func.sum(DividaAtiva.saldo), 0)).where(*w)))  # noqa: E731
    # DP-P1: 10 × 50 com 3 pagas = 350 (não 500 + 500 = 1000, nem 850 com o original)
    assert soma(DividaAtiva.codi == "ZZTESTE0000001", DividaAtiva.em_aberto.is_(True)) == pytest.approx(350.0)
    assert db.scalar(select(func.count()).select_from(DividaAtiva).where(DividaAtiva.codi == "ZZTESTE0000001", DividaAtiva.em_aberto.is_(True))) == 7
    # total em aberto: 350 (P) + 120 (D) + 30 (G) + 0 (B) = 500; N, Q, R e extinta fora
    assert soma(DividaAtiva.em_aberto.is_(True)) == pytest.approx(500.0)
    d2 = db.scalar(select(DividaAtiva).where(DividaAtiva.codi == "ZZTESTE0000002"))
    assert (float(d2.saldo_bruto), float(d2.pago_conta_corrente), float(d2.saldo)) == (200.0, 80.0, 120.0)
    b = db.scalar(select(DividaAtiva).where(DividaAtiva.codi == "ZZTESTE0000004"))
    assert float(b.saldo) == 0 and b.em_aberto is False
    q = db.scalar(select(DividaAtiva).where(DividaAtiva.codi == "ZZTESTE0000005"))
    assert q.qualidade == "quitado_sem_baixa" and q.em_aberto is False
    r = db.scalar(select(DividaAtiva).where(DividaAtiva.codi == "ZZTESTE0000006"))
    assert r.em_aberto is False and r.pago_conta_corrente is None   # Vr_Pago 0 não conta
    n = db.scalar(select(DividaAtiva).where(DividaAtiva.compo == "N"))
    assert n.em_aberto is False


def test_kpi_e_cubo_usam_em_aberto(prata):
    from app.gold import cubos
    db = prata
    cubos.reconstruir(db)
    total, insc = db.execute(text("SELECT COALESCE(sum(saldo), 0), COALESCE(sum(inscricoes), 0) FROM gold.cubo_divida")).one()
    assert float(total) == pytest.approx(500.0) and insc == 9   # 7 P + D + G
    from app.services import vinculos
    assert vinculos._divida(db, codi="ZZTESTE0000005") == 0     # Q sem baixa não vira dívida no vínculo
    assert vinculos._divida(db, codi="ZZTESTE0000002") == pytest.approx(120.0)


def test_migracao_0046_aditiva(db):
    cols = {r[0] for r in db.execute(text("SELECT column_name FROM information_schema.columns "
                                          "WHERE table_schema = 'silver' AND table_name = 'dividas'"))}
    assert {"sequ", "compo", "saldo_bruto", "pago_conta_corrente", "em_aberto", "qualidade", "inscricao"} <= cols
    import pathlib
    src = pathlib.Path(__file__).parents[1].joinpath("alembic/versions/0046_divida_saldo_liquido.py").read_text()
    sqls = " ".join(re.findall(r'op\.execute\((.*)\)', src)).upper()
    assert "ADD COLUMN IF NOT EXISTS" in sqls and "DROP" not in sqls and "DELETE" not in sqls
