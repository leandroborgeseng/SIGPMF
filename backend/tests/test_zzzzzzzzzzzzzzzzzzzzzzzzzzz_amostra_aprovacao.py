"""PR-A (UXA1–UXA5, CT1–CT4, CT13, CT14): botão de amostra de volta, aprovação da amostra e regressão.

Só dados sintéticos (Leitor/OrigemMock ZZTESTE do test_zzz_carga_completa) e o banco *_test do conftest.
Nenhum teste lê a origem real nem chama POST /franca/imoveis/atualizar.
"""
from sqlalchemy import select

from app.models.controle import Auditoria, Execucao
from app.services import carga_completa as ccomp
from app.services import carga_conector as cc
from tests.test_zzz_carga_completa import Leitor


def _amostra_manual(db, conector="cemiterio"):
    """Amostra sintética concluída com a marca aprovacao=manual (como a rota grava a partir desta versão)."""
    sis = cc.CONECTORES[conector][0]
    ex = cc.executar_amostra(db, "admin@aion.local", conector, leitor=Leitor(sis))
    assert ex.status in ("sucesso", "sem_novidades"), ex.mensagem
    db.add(Auditoria(usuario="admin@aion.local", acao="amostra_conector_iniciada", alvo=f"execucao:{ex.id}",
                     detalhe={"conector": conector, "aprovacao": "manual"}))
    db.commit()
    return ex


def _base(client, tokens, conector):
    return next(b for b in client.get("/api/conectores", headers=tokens["admin"]).json()["bases"]
                if b["conector_id"] == conector)


def _rotas_post():
    from app.api.rotas import api
    return {"/api" + r.path for r in api.routes if "POST" in (getattr(r, "methods", None) or set())}


def test_ct14_nenhuma_fonte_exige_amostra_sem_jeito_de_roda_la(client, tokens):
    """CT14 / UXA5: toda base que exige amostra tem conector com rota POST de amostra e vem marcada para o botão."""
    rotas = _rotas_post()
    assert "/api/conectores/{conector}/amostra" in rotas and "/api/conectores/sil/amostra" in rotas
    assert "/api/conectores/{conector}/amostra/{eid}/aprovar" in rotas
    for conector in ccomp.CONECTORES_COMPLETA:
        assert conector in cc.CONECTORES, f"{conector} exige amostra mas não tem conector de amostra"
    bases = client.get("/api/conectores", headers=tokens["admin"]).json()["bases"]
    exigem = [b for b in bases if b.get("completa_disponivel")]
    assert {"divida", "financeiro", "sil"} <= {b["conector_id"] for b in exigem}
    for b in exigem:
        assert b["exige_amostra"] is True and b["tem_conector"] is True, b["nome"]
        assert b["conector_id"] in cc.CONECTORES, b["nome"]
        assert b["amostra_limite"] >= 1
        if not b["completa_liberada"]:
            assert '"Rodar amostra"' in b["completa_motivo"] or '"Aprovar amostra"' in b["completa_motivo"], b["nome"]


def test_ct1_ct13_sem_amostra_motivo_diz_onde_clicar(client, tokens, db):
    for conector in ("divida", "financeiro", "sil"):
        if ccomp.ultima_amostra(db, conector) is None:
            m = ccomp.motivo_bloqueio(db, conector)
            assert m == ccomp.SEM_AMOSTRA and '"Rodar amostra"' in m and "Conectores > Bases de origem" in m


def test_ct2_amostra_nova_so_libera_depois_de_aprovar(client, tokens, db):
    ex = _amostra_manual(db)
    assert ccomp.motivo_bloqueio(db, "cemiterio") == ccomp.AMOSTRA_PENDENTE
    b = _base(client, tokens, "cemiterio")
    assert b["completa_liberada"] is False and '"Aprovar amostra"' in b["completa_motivo"]
    assert b["amostra"]["id"] == ex.id and b["amostra"]["aprovada"] is False and b["amostra"]["lidos"] == ex.lidos
    assert {"lidos", "gravados", "com_erro", "status"} <= set(b["amostra"])
    # operador não aprova
    assert client.post(f"/api/conectores/cemiterio/amostra/{ex.id}/aprovar", headers=tokens["operador"]).status_code == 403
    r = client.post(f"/api/conectores/cemiterio/amostra/{ex.id}/aprovar", headers=tokens["admin"])
    assert r.status_code == 200 and r.json()["aprovada"] is True
    db.expire_all()
    assert ccomp.motivo_bloqueio(db, "cemiterio") is None
    assert _base(client, tokens, "cemiterio")["completa_liberada"] is True
    # CT4: auditado com quem, quando e SHA
    a = db.scalars(select(Auditoria).where(Auditoria.acao == "amostra_aprovada", Auditoria.alvo == f"execucao:{ex.id}")).one()
    assert a.usuario == "admin@aion.local" and a.criado_em is not None and "sha" in a.detalhe
    # aprovar de novo é idempotente (não duplica a auditoria)
    assert client.post(f"/api/conectores/cemiterio/amostra/{ex.id}/aprovar", headers=tokens["admin"]).status_code == 200
    assert len(db.scalars(select(Auditoria).where(Auditoria.acao == "amostra_aprovada",
                                                  Auditoria.alvo == f"execucao:{ex.id}")).all()) == 1


def test_ct2_nao_aprova_amostra_antiga_nem_com_falha(client, tokens, db):
    velha = _amostra_manual(db)
    nova = _amostra_manual(db)
    r = client.post(f"/api/conectores/cemiterio/amostra/{velha.id}/aprovar", headers=tokens["admin"])
    assert r.status_code == 409
    db.add(Execucao(tipo=cc.TIPO_AMOSTRA, sistema="CEMITERIO", status="falha", usuario="t"))
    db.commit()
    falha = ccomp.ultima_amostra(db, "cemiterio")
    assert falha.id != nova.id
    r = client.post(f"/api/conectores/cemiterio/amostra/{falha.id}/aprovar", headers=tokens["admin"])
    assert r.status_code == 409 and "rode de novo" in r.json()["detail"]
    assert ccomp.motivo_bloqueio(db, "cemiterio") == ccomp.SEM_AMOSTRA  # a regra da amostra segue valendo


def test_ct3_amostra_de_antes_da_aprovacao_vale_como_aprovada(client, tokens, db):
    """CT3: amostra concluída antes desta versão (sem a marca aprovacao=manual) aparece como aprovada."""
    sis = cc.CONECTORES["cemiterio"][0]
    ex = cc.executar_amostra(db, "admin@aion.local", "cemiterio", leitor=Leitor(sis))
    assert ccomp.amostra_aprovada(db, ex) and ccomp.motivo_bloqueio(db, "cemiterio") is None
    assert _base(client, tokens, "cemiterio")["amostra"]["aprovada"] is True


def test_rota_amostra_grava_marca_de_aprovacao_manual(client, tokens, db, monkeypatch):
    sis = cc.CONECTORES["cemiterio"][0]
    monkeypatch.setattr(cc, "leitor_de", lambda conector, cfg: Leitor(sis))
    r = client.post("/api/conectores/cemiterio/amostra", headers=tokens["admin"])
    assert r.status_code == 202
    eid = r.json()["id"]
    db.expire_all()
    ini = db.scalars(select(Auditoria).where(Auditoria.acao == "amostra_conector_iniciada",
                                             Auditoria.alvo == f"execucao:{eid}")).one()
    assert ini.detalhe["aprovacao"] == "manual"
