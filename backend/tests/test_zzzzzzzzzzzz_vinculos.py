# Usa a bronze/prata sintética do tributário (fixture_tributario). Só dados sintéticos.
"""Onda 6 — explorador de vínculos (VN1–VN4): um nível por pedido, ids opacos, motivo, limite e auditoria."""
import re

from sqlalchemy import select

from app.models.camadas import ImovelPossuidor
from app.models.controle import Auditoria
from tests.test_zzzzzzzzz_tributario import _carga, _cid_sa1, origem  # noqa: F401

M = "Cobrança de dívida ativa, processo de teste"


def _get(client, tokens, no, perfil="admin_lgpd", **kw):
    params = {"motivo": M, **kw}
    return client.get(f"/api/vinculos/{no}", params=params, headers=tokens[perfil])


def test_vn1_pessoa_imovel_possuidores_e_volta(client, tokens, db, origem):  # noqa: F811
    _carga(db, "saude")
    for c in ("iptu", "financeiro", "divida"):
        _carga(db, c)
    cid = _cid_sa1(db)
    r = _get(client, tokens, f"pf:{cid}")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["no"]["tipo"] == "PF" and "*" in d["no"]["doc"]
    assert any(f["texto"].startswith("Dívida ativa") for f in d["no"]["flags"])
    im = [v for v in d["vizinhos"] if v["id"] == "im:01111010010100"]
    assert im and im[0]["grupo"] == "Imóveis" and "desde 2015" in im[0]["relacao"]
    viz = _get(client, tokens, "im:01111010010100", nivel=1).json()["vizinhos"]
    ids = {v["id"] for v in viz}
    assert f"pf:{cid}" in ids                                        # volta para a pessoa
    anterior = [v for v in viz if v["id"].startswith("ps:")]
    assert anterior and anterior[0]["relacao"].endswith("posse anterior") and "*" in anterior[0]["doc"]
    # nenhum CPF/CNPJ completo em lugar nenhum da resposta (ids opacos, documento mascarado)
    for p in db.scalars(select(ImovelPossuidor)):
        assert p.documento not in r.text and p.documento not in str(viz)
    assert not re.search(r"\d{11,}", str([v["id"] for v in viz if not v["id"].startswith("im:")]))
    # possuidor PJ leva aos imóveis dele
    pj = _get(client, tokens, "im:02222020020200", nivel=1).json()["vizinhos"][0]
    assert pj["tipo"] == "PJ" and pj["doc"].startswith("**.***.***/")
    assert [v["id"] for v in _get(client, tokens, pj["id"], nivel=2).json()["vizinhos"]] == ["im:02222020020200"]


def test_vn2_permissoes_motivo_nivel_e_auditoria(client, tokens, db, origem):  # noqa: F811
    _carga(db, "saude")
    _carga(db, "iptu")
    cid = _cid_sa1(db)
    assert _get(client, tokens, f"pf:{cid}", perfil="leitura").status_code == 403
    assert _get(client, tokens, f"pf:{cid}", perfil="operador").status_code == 403
    assert client.get(f"/api/vinculos/pf:{cid}", params={"motivo": "curto"}, headers=tokens["admin"]).status_code == 422
    assert _get(client, tokens, f"pf:{cid}", nivel=4).status_code == 422
    assert _get(client, tokens, "pf:nao-existe").status_code == 404
    assert _get(client, tokens, "xx:1").status_code == 404
    assert _get(client, tokens, "ps:abc").status_code == 404
    assert _get(client, tokens, f"pf:{cid}", processo="SEI 123/2026").status_code == 200
    a = db.scalar(select(Auditoria).where(Auditoria.acao == "consulta_vinculos").order_by(Auditoria.id.desc()))
    assert a.alvo == f"cidadao:{cid}" and a.detalhe["motivo"] == M and a.detalhe["processo"] == "SEI 123/2026"
    assert client.get(f"/api/vinculos/pf:{cid}").status_code == 401
