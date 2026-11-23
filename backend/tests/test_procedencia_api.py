"""A API de indicadores devolve o sistema e a data da série (dado fictício)."""
from datetime import UTC, datetime

from sqlalchemy import delete

from app.models.camadas import Indicador


def test_indicadores_expoe_sistema_e_data(client, tokens, db):
    quando = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)
    db.add(Indicador(serie="saude_zz_ficticio", periodo="2026-10", dim="", valor=1500, atualizado_em=quando))
    db.commit()
    r = client.get("/api/indicadores", params={"series": "saude_zz_ficticio"}, headers=tokens["leitura"])
    assert r.status_code == 200, r.text
    corpo = r.json()
    assert corpo["series"]["saude_zz_ficticio"][0]["valor"] == 1500
    proc = corpo["procedencia"]["saude_zz_ficticio"]
    assert proc["sistema"] == "SAUDE"
    assert proc["fonte_atualizado_em"].startswith("2026-11-04")
    db.execute(delete(Indicador).where(Indicador.serie == "saude_zz_ficticio"))
    db.commit()
