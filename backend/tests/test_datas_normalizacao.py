from datetime import date, datetime

import pytest

from app.util.datas import normaliza_data, normaliza_data_iso


@pytest.mark.parametrize("v,esperado", [
    ("1980-05-12", date(1980, 5, 12)),
    ("1980-05-12 00:00:00", date(1980, 5, 12)),
    ("1980-05-12T00:00:00", date(1980, 5, 12)),
    ("1980-05-12T00:00:00.000Z", date(1980, 5, 12)),
    ("12/05/1980", date(1980, 5, 12)),
    ("1/2/1980", date(1980, 2, 1)),
    (datetime(1980, 5, 12, 3, 4), date(1980, 5, 12)),
    (date(1980, 5, 12), date(1980, 5, 12)),
    (None, None), ("", None), ("lixo", None), ("31/02/1980", None), ("1980-13-01", None), (12345, None),
])
def test_normaliza_data(v, esperado):
    assert normaliza_data(v) == esperado


def test_normaliza_data_iso():
    assert normaliza_data_iso("12/05/1980 00:00") == "1980-05-12"
    assert normaliza_data_iso("xx") is None


def test_rota_detalhe_normaliza_data_legada_e_tolera_nulos(client, tokens, db):
    """Gold gravada antes da correção ('AAAA-MM-DD HH:MM:SS') sai como AAAA-MM-DD; sem vínculos não dá 5xx."""
    from sqlalchemy import delete

    from app.models.camadas import CidadaoSistemas, DadoGold
    cid = "sintetico-tela-branca"
    db.add(CidadaoSistemas(cidadao_id=cid, nome="Fulano Sintético", sistemas=["SAUDE"], dominios=["saude"]))
    db.add(DadoGold(cidadao_id=cid, dominio="identificacao", campo="data_nascimento", valor="1980-05-12 00:00:00",
                    sistema="SAUDE", fonte_atualizado_em=None, mais_recente=True))
    db.commit()
    try:
        r = client.get(f"/api/gold/cidadaos/{cid}", headers=tokens["leitura"])
        assert r.status_code == 200
        corpo = r.json()
        assert corpo["cpf"] is None and corpo["enderecos"] == []
        assert corpo["dados"][0]["valor"] == "1980-05-12"
    finally:
        db.execute(delete(DadoGold).where(DadoGold.cidadao_id == cid))
        db.execute(delete(CidadaoSistemas).where(CidadaoSistemas.cidadao_id == cid))
        db.commit()
