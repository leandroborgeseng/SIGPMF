"""PR-06: RBAC por secretaria (R1–R4) e tela de TI (R5/R6, UXV1). Só dados sintéticos ZZTESTE; usuários @aion.local."""
import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import select, text

from app.core.security import hash_senha
from app.models.camadas import Cidadao, CidadaoSistemas, DadoGold, EscolaAluno
from app.models.controle import Auditoria, AuditoriaAcesso, Usuario, UsuarioSecretaria
from tests.conftest import SENHA

HOJE = date.today()


@pytest.fixture
def rbac(monkeypatch):
    monkeypatch.setenv("SIGPMF_RBAC_SECRETARIA", "true")


def _usuario(db, client, perfil="leitura"):
    email = f"zzteste-{uuid.uuid4().hex[:8]}@aion.local"
    u = Usuario(email=email, nome=f"ZZTESTE {perfil} {email[8:16]}", perfil=perfil, senha_hash=hash_senha(SENHA))
    db.add(u)
    db.commit()
    r = client.post("/api/auth/login", json={"usuario": email, "senha": SENHA})
    assert r.status_code == 200
    return u, {"Authorization": f"Bearer {r.json()['access_token']}"}


def _vinculo(db, u, sec, inicio=None, fim=None):
    db.add(UsuarioSecretaria(usuario_id=u.id, secretaria=sec, vigencia_inicio=inicio or HOJE - timedelta(days=30),
                             vigencia_fim=fim, cadastrado_por="admin@aion.local"))
    db.commit()


def _cidadao(db, nasc=date(1980, 1, 1), dominios=("saude", "tributario")):
    cid = str(uuid.uuid4())
    db.add(Cidadao(cidadao_id=cid, nome="ZZTESTE CIDADAO", data_nascimento=nasc))
    db.add(CidadaoSistemas(cidadao_id=cid, nome="ZZTESTE CIDADAO", sistemas=["ZZ"], dominios=list(dominios)))
    db.add(DadoGold(cidadao_id=cid, dominio="saude", campo="cns", valor="ZZTESTE-CNS", sistema="ZZ"))
    db.add(DadoGold(cidadao_id=cid, dominio="tributario", campo="inscricao", valor="ZZTESTE-INSC", sistema="ZZ"))
    db.commit()
    return cid


def _acessos(db, email):
    db.expire_all()
    return db.scalar(select(text("count(*)")).select_from(AuditoriaAcesso).where(AuditoriaAcesso.usuario == email))


# ---------- R1 ----------
def test_r1_vinculo_vigente_ve_e_grava_auditoria_acesso(client, db, rbac):
    u, h = _usuario(db, client)
    _vinculo(db, u, "SAUDE")
    cid = _cidadao(db)
    r = client.get(f"/api/gold/cidadaos/{cid}/saude", headers=h)
    assert r.status_code == 200
    assert _acessos(db, u.email) == 1
    a = db.scalar(select(AuditoriaAcesso).where(AuditoriaAcesso.usuario == u.email))
    assert (a.secretaria, a.recurso, a.alvo) == ("SAUDE", "saude", f"cidadao:{cid}")


def test_r1_ficha_mostra_so_dominios_da_secretaria(client, db, rbac):
    u, h = _usuario(db, client)
    _vinculo(db, u, "SAUDE")
    cid = _cidadao(db)
    r = client.get(f"/api/gold/cidadaos/{cid}", headers=h)
    assert r.status_code == 200
    assert {d["dominio"] for d in r.json()["dados"]} == {"saude"}


def test_r1_educacao_sem_mascara_para_secretaria(client, db, rbac):
    u, h = _usuario(db, client)
    _vinculo(db, u, "EDUCACAO")
    cid = _cidadao(db, nasc=HOJE - timedelta(days=365 * 10))
    db.add(EscolaAluno(alu_id=int(uuid.uuid4().int % 10**12), nome="ZZTESTE ALUNO MENOR", data_nascimento=HOJE - timedelta(days=3650),
                       cpf="00000000191", cidadao_id=cid, status_vinculo="vinculado", situacao=1, excluido=False))
    db.commit()
    r = client.get(f"/api/gold/cidadaos/{cid}/educacao", headers=h)
    assert r.status_code == 200
    al = r.json()["alunos"][0]
    assert al["nome"] == "ZZTESTE ALUNO MENOR" and al["mascarado"] is False
    assert al["cpf"] == "00000000191"  # TL 29/10: CPF completo para a secretaria vinculada
    db.expire_all()
    a = db.scalar(select(AuditoriaAcesso).where(AuditoriaAcesso.usuario == u.email))
    assert a.revelou_cpf is True and a.recurso == "educacao"


# ---------- R2: teste negativo obrigatório ----------
@pytest.mark.parametrize("caso", ["outra_secretaria", "sem_vinculo", "vencido", "removido", "futuro"])
@pytest.mark.parametrize("rota", ["/saude", "/tributario", "/educacao"])
def test_r2_negativo_403_sem_dado(client, db, rbac, caso, rota):
    u, h = _usuario(db, client)
    alvo = {"/saude": "SAUDE", "/tributario": "FAZENDA", "/educacao": "EDUCACAO"}[rota]
    if caso == "outra_secretaria":
        _vinculo(db, u, "ADMINISTRACAO")
    elif caso == "vencido":
        _vinculo(db, u, alvo, inicio=HOJE - timedelta(days=60), fim=HOJE - timedelta(days=1))
    elif caso == "removido":
        _vinculo(db, u, alvo)
        v = db.scalar(select(UsuarioSecretaria).where(UsuarioSecretaria.usuario_id == u.id))
        v.removido_em, v.removido_por, v.motivo_remocao = __import__("datetime").datetime.now(), "admin@aion.local", "ZZTESTE"
        db.commit()
    elif caso == "futuro":
        _vinculo(db, u, alvo, inicio=HOJE + timedelta(days=1))
    cid = _cidadao(db)
    r = client.get(f"/api/gold/cidadaos/{cid}{rota}", headers=h)
    assert r.status_code == 403
    corpo = r.text
    assert "ZZTESTE" not in corpo and cid not in corpo
    assert set(r.json()) == {"detail"}
    assert _acessos(db, u.email) == 0
    neg = db.scalar(select(Auditoria).where(Auditoria.usuario == u.email, Auditoria.acao == "acesso_secretaria_negado"))
    assert neg is not None and "ZZTESTE" not in str(neg.detalhe)


@pytest.mark.parametrize("rota", ["/api/silver/cidadaos", "/api/silver/cidadaos.csv"])
def test_r2_lista_nominal_sem_vinculo_403_sem_contagem(client, db, rbac, rota):
    _cidadao(db)
    u, h = _usuario(db, client, "operador")
    r = client.get(rota, headers=h)
    assert r.status_code == 403 and "ZZTESTE" not in r.text


def test_r2_cidadao_inexistente_sem_vinculo_e_403_nao_404(client, db, rbac):
    _u, h = _usuario(db, client)
    assert client.get(f"/api/gold/cidadaos/{uuid.uuid4()}", headers=h).status_code == 403


def test_r2_ficha_vinculo_vencido_403(client, db, rbac):
    u, h = _usuario(db, client)
    _vinculo(db, u, "SAUDE", inicio=HOJE - timedelta(days=10), fim=HOJE - timedelta(days=1))
    cid = _cidadao(db)
    for rota in ("", "/linha-do-tempo", "/sistemas"):
        assert client.get(f"/api/gold/cidadaos/{cid}{rota}", headers=h).status_code == 403


# ---------- R3 ----------
def test_r3_prefeito_ve_tudo_com_auditoria(client, db, rbac):
    u, h = _usuario(db, client)
    _vinculo(db, u, "TODAS")
    cid = _cidadao(db, nasc=HOJE - timedelta(days=365 * 5))
    for rota in ("", "/saude", "/tributario", "/educacao"):
        assert client.get(f"/api/gold/cidadaos/{cid}{rota}", headers=h).status_code == 200
    r = client.get(f"/api/gold/cidadaos/{cid}", headers=h)
    assert {d["dominio"] for d in r.json()["dados"]} == {"saude", "tributario"}
    db.expire_all()
    secs = set(db.scalars(select(AuditoriaAcesso.secretaria).where(AuditoriaAcesso.usuario == u.email)))
    assert secs == {"TODAS"} and _acessos(db, u.email) == 5


# ---------- R4 ----------
def test_r4_menor_so_educacao_clinico_so_saude(client, db, rbac):
    cid = _cidadao(db, nasc=HOJE - timedelta(days=365 * 12), dominios=["saude", "tributario", "educacao"])
    fz, hf = _usuario(db, client)
    _vinculo(db, fz, "FAZENDA")
    assert client.get(f"/api/gold/cidadaos/{cid}", headers=hf).status_code == 403
    assert client.get(f"/api/gold/cidadaos/{cid}/tributario", headers=hf).status_code == 403
    sa, hs = _usuario(db, client)
    _vinculo(db, sa, "SAUDE")
    assert client.get(f"/api/gold/cidadaos/{cid}", headers=hs).status_code == 403  # TL 29/10: menor só Educação
    assert client.get(f"/api/gold/cidadaos/{cid}/saude", headers=hs).status_code == 200  # clínico é da Saúde
    ed, he = _usuario(db, client)
    _vinculo(db, ed, "EDUCACAO")
    assert client.get(f"/api/gold/cidadaos/{cid}", headers=he).status_code == 200
    assert client.get(f"/api/gold/cidadaos/{cid}/saude", headers=he).status_code == 403  # clínico só Saúde
    adulto = _cidadao(db)
    assert client.get(f"/api/gold/cidadaos/{adulto}/tributario", headers=hf).status_code == 200


def test_r4_saude_nominal_sem_conteudo_clinico(client, db, rbac):
    u, h = _usuario(db, client)
    _vinculo(db, u, "SAUDE")
    cid = _cidadao(db)
    corpo = client.get(f"/api/gold/cidadaos/{cid}/saude", headers=h).json()
    assert set(corpo) == {"cidadao_id", "proximos", "atendimentos", "vacinas"}  # sem CID/diagnóstico/prescrição


def test_regra_desligada_nao_muda_comportamento(client, db, monkeypatch):
    monkeypatch.delenv("SIGPMF_RBAC_SECRETARIA", raising=False)
    _u, h = _usuario(db, client)
    cid = _cidadao(db)
    assert client.get(f"/api/gold/cidadaos/{cid}/saude", headers=h).status_code == 200


# ---------- R5 / R6 ----------
B = "/api/ti/acesso-secretaria"


@pytest.mark.parametrize("perfil", ["admin_lgpd", "operador", "leitura"])
def test_r6_so_admin_na_tela_de_ti(client, db, tokens, perfil):
    alvo, _ = _usuario(db, client)
    for rota, corpo in (("/secretarias", {}), ("/usuarios/buscar", {"termo": "zzteste"}),
                        ("/vinculos/listar", {"usuario_id": alvo.id}), ("/vinculos/historico", {"usuario_id": alvo.id}),
                        ("/vinculos/adicionar", {"usuario_id": alvo.id, "secretaria": "SAUDE"}),
                        ("/vinculos/remover", {"vinculo_id": 1, "versao": 1, "motivo": "x" * 12})):
        r = client.post(B + rota, json=corpo, headers=tokens[perfil])
        assert r.status_code == 403, rota
        assert r.json() == {"detail": "Você não tem permissão para esta tela."}
    db.expire_all()
    assert db.scalar(select(text("count(*)")).select_from(UsuarioSecretaria)
                     .where(UsuarioSecretaria.usuario_id == alvo.id)) == 0


def test_r5_fluxo_completo_auditado(client, db, tokens):
    h = tokens["admin"]
    alvo, _ = _usuario(db, client)
    r = client.post(B + "/usuarios/buscar", json={"termo": alvo.email[:14]}, headers=h)
    assert r.status_code == 200 and [x["id"] for x in r.json()] == [alvo.id]
    assert client.post(B + "/usuarios/buscar", json={"termo": "zz"}, headers=h).status_code == 422
    r = client.post(B + "/vinculos/adicionar", json={"usuario_id": alvo.id, "secretaria": "SAUDE"}, headers=h)
    assert r.status_code == 201
    v = r.json()
    assert v["cadastrado_por"] == "Teste admin" and v["inicio"] == HOJE.isoformat() and v["fim"] is None
    # duplicado = conflito
    r = client.post(B + "/vinculos/adicionar", json={"usuario_id": alvo.id, "secretaria": "SAUDE"}, headers=h)
    assert r.status_code == 409
    # fim antes do início
    r = client.post(B + "/vinculos/adicionar", json={"usuario_id": alvo.id, "secretaria": "FAZENDA",
                                                     "inicio": "2026-10-10", "fim": "2026-10-01"}, headers=h)
    assert r.status_code == 422
    # editar com versão velha = 409
    r = client.post(B + "/vinculos/editar", json={"vinculo_id": v["id"], "versao": 1, "inicio": "2026-01-01", "fim": "2026-01-31"},
                    headers=h)
    assert r.status_code == 200 and r.json()["vencido"] is True and r.json()["versao"] == 2
    r = client.post(B + "/vinculos/editar", json={"vinculo_id": v["id"], "versao": 1, "inicio": "2026-01-01"}, headers=h)
    assert r.status_code == 409 and "outra pessoa" in r.json()["detail"]
    # remover exige motivo
    r = client.post(B + "/vinculos/remover", json={"vinculo_id": v["id"], "versao": 2, "motivo": "curto"}, headers=h)
    assert r.status_code == 422
    r = client.post(B + "/vinculos/remover", json={"vinculo_id": v["id"], "versao": 2, "motivo": "ZZTESTE saiu da secretaria"},
                    headers=h)
    assert r.status_code == 200
    lst = client.post(B + "/vinculos/listar", json={"usuario_id": alvo.id}, headers=h).json()
    assert lst["vinculos"] == []
    # remoção lógica: a linha continua no banco
    db.expire_all()
    linha = db.get(UsuarioSecretaria, v["id"])
    assert linha is not None and linha.removido_em is not None
    hist = client.post(B + "/vinculos/historico", json={"usuario_id": alvo.id}, headers=h).json()
    assert [x["acao"] for x in hist] == ["Removeu", "Editou a vigência", "Adicionou"]
    assert hist[0]["motivo"] == "ZZTESTE saiu da secretaria" and hist[0]["quem"] == "Teste admin"


def test_r5_prefeito_nao_removido_pela_tela(client, db, tokens):
    alvo, _ = _usuario(db, client)
    v = client.post(B + "/vinculos/conceder-prefeito", json={"usuario_id": alvo.id, "motivo": "ZZTESTE posse do prefeito"}, headers=tokens["admin_lgpd"]).json()
    assert v["prefeito"] is True and v["secretaria_nome"] == "Todas as secretarias (prefeito)"
    r = client.post(B + "/vinculos/remover", json={"vinculo_id": v["id"], "versao": 1, "motivo": "ZZTESTE motivo longo"},
                    headers=tokens["admin"])
    assert r.status_code == 422


def test_r5_vinculo_removido_corta_acesso_na_hora(client, db, tokens, rbac):
    u, h = _usuario(db, client)
    cid = _cidadao(db)
    v = client.post(B + "/vinculos/adicionar", json={"usuario_id": u.id, "secretaria": "SAUDE"}, headers=tokens["admin"]).json()
    assert client.get(f"/api/gold/cidadaos/{cid}/saude", headers=h).status_code == 200
    client.post(B + "/vinculos/remover", json={"vinculo_id": v["id"], "versao": 1, "motivo": "ZZTESTE mudou de setor"},
                headers=tokens["admin"])
    assert client.get(f"/api/gold/cidadaos/{cid}/saude", headers=h).status_code == 403


def test_uxv1_rotas_ti_sem_termo_nem_id_na_url(client):
    from app.api.acesso_ti import ti
    rotas = list(ti.routes)
    assert len(rotas) == 12 and all("{" not in r.path and r.methods == {"POST"} for r in rotas)


def test_auditoria_acesso_somente_insercao(db, client, rbac):
    u, h = _usuario(db, client)
    _vinculo(db, u, "SAUDE")
    client.get(f"/api/gold/cidadaos/{_cidadao(db)}/saude", headers=h)
    with pytest.raises(Exception):  # noqa: B017
        db.execute(text("DELETE FROM controle.auditoria_acesso"))
    db.rollback()


# ---------- TL 29/10: CPF completo, tabela de referência e prefeito ----------
def _cpf(db, cid, valor="52998224725"):
    from app.models.camadas import Identificador
    db.add(Identificador(cidadao_id=cid, tipo="cpf", valor=valor))
    db.commit()


def test_cpf_completo_para_secretaria_vinculada_e_auditado(client, db, rbac):
    u, h = _usuario(db, client)
    _vinculo(db, u, "SAUDE")
    cid = _cidadao(db)
    _cpf(db, cid, "39053344705")
    r = client.get(f"/api/gold/cidadaos/{cid}", headers=h).json()
    assert r["cpf"] == "39053344705" and r["cpf_mascarado"] is False
    db.expire_all()
    a = db.scalar(select(AuditoriaAcesso).where(AuditoriaAcesso.usuario == u.email))
    assert a.revelou_cpf is True and a.recurso == "ficha"


def test_cpf_mascarado_com_regra_desligada(client, db, monkeypatch):
    monkeypatch.delenv("SIGPMF_RBAC_SECRETARIA", raising=False)
    _u, h = _usuario(db, client)
    cid = _cidadao(db)
    _cpf(db, cid, "11144477735")
    r = client.get(f"/api/gold/cidadaos/{cid}", headers=h).json()
    assert r["cpf"] != "11144477735" and r["cpf_mascarado"] is True


def test_admin_sem_vinculo_403(client, db, tokens, rbac):
    cid = _cidadao(db)
    assert client.get(f"/api/gold/cidadaos/{cid}", headers=tokens["admin"]).status_code == 403


def test_referencia_seed_e_cemiterio_a_confirmar(client, tokens):
    r = client.post(B + "/referencia/listar", json={}, headers=tokens["admin"]).json()
    assert {x["codigo"] for x in r["secretarias"]} >= {"EDUCACAO", "SAUDE", "FAZENDA", "ASSISTENCIA_SOCIAL", "ADMINISTRACAO",
                                                       "SERVICOS_URBANOS", "TODAS"}
    cem = next(d for d in r["dominios"] if d["dominio"] == "cemiterio")
    assert cem["secretaria"] == "SERVICOS_URBANOS" and cem["a_confirmar"] is True


def test_ti_ajusta_referencia_sem_deploy_e_vinculo_valida_tabela(client, db, tokens, rbac):
    h = tokens["admin"]
    assert client.post(B + "/referencia/secretaria", json={"codigo": "ZZTESTE_SEC", "nome": "ZZTESTE Secretaria"},
                       headers=h).status_code == 200
    alvo, ha = _usuario(db, client)
    assert client.post(B + "/vinculos/adicionar", json={"usuario_id": alvo.id, "secretaria": "ZZTESTE_SEC"}, headers=h).status_code == 201
    assert client.post(B + "/vinculos/adicionar", json={"usuario_id": alvo.id, "secretaria": "INEXISTENTE"}, headers=h).status_code == 422
    client.post(B + "/referencia/secretaria", json={"codigo": "ZZTESTE_SEC", "nome": "ZZTESTE Secretaria", "ativo": False}, headers=h)
    outro, _ = _usuario(db, client)
    assert client.post(B + "/vinculos/adicionar", json={"usuario_id": outro.id, "secretaria": "ZZTESTE_SEC"}, headers=h).status_code == 422
    assert client.post(B + "/referencia/secretaria", json={"codigo": "TODAS", "nome": "Todas as secretarias (prefeito)", "ativo": False},
                       headers=h).status_code == 422
    # domínio remapeado sem deploy: tributário passa a ser da ZZTESTE_SEGUNDA e a ficha acompanha
    client.post(B + "/referencia/secretaria", json={"codigo": "ZZTESTE_SEGUNDA", "nome": "ZZTESTE Sec 2"}, headers=h)
    u, hu = _usuario(db, client)
    _vinculo(db, u, "ZZTESTE_SEGUNDA")
    cid = _cidadao(db)
    assert client.post(B + "/referencia/dominio", json={"dominio": "tributario", "secretaria": "ZZTESTE_SEGUNDA"}, headers=h).status_code == 200
    try:
        assert {d["dominio"] for d in client.get(f"/api/gold/cidadaos/{cid}", headers=hu).json()["dados"]} == {"tributario"}
    finally:
        client.post(B + "/referencia/dominio", json={"dominio": "tributario", "secretaria": "FAZENDA"}, headers=h)
    assert client.post(B + "/referencia/dominio", json={"dominio": "x_y", "secretaria": "NAO_EXISTE"}, headers=h).status_code == 422
    db.expire_all()
    assert db.scalar(select(Auditoria).where(Auditoria.acao == "dominio_secretaria_salvar")) is not None
    for perfil in ("admin_lgpd", "operador"):
        assert client.post(B + "/referencia/secretaria", json={"codigo": "ZZ_X", "nome": "ZZTESTE X"},
                           headers=tokens[perfil]).status_code == 403


def test_prefeito_so_admin_lgpd_remove_com_motivo_auditado(client, db, tokens):
    alvo, _ = _usuario(db, client)
    v = client.post(B + "/vinculos/conceder-prefeito", json={"usuario_id": alvo.id, "motivo": "ZZTESTE posse do prefeito"}, headers=tokens["admin_lgpd"]).json()
    corpo = {"vinculo_id": v["id"], "versao": 1, "motivo": "ZZTESTE fim do mandato"}
    assert client.post(B + "/vinculos/remover", json=corpo, headers=tokens["admin"]).status_code == 422
    for perfil in ("admin", "operador", "leitura"):
        assert client.post(B + "/vinculos/remover-prefeito", json=corpo, headers=tokens[perfil]).status_code == 403
    r = client.post(B + "/vinculos/remover-prefeito", json={**corpo, "motivo": "curto"}, headers=tokens["admin_lgpd"])
    assert r.status_code == 422
    r = client.post(B + "/vinculos/remover-prefeito", json=corpo, headers=tokens["admin_lgpd"])
    assert r.status_code == 200
    db.expire_all()
    linha = db.get(UsuarioSecretaria, v["id"])
    assert linha.removido_em is not None and linha.removido_por == "admin_lgpd@aion.local"
    a = db.scalar(select(Auditoria).where(Auditoria.acao == "vinculo_prefeito_remover", Auditoria.alvo == f"usuario:{alvo.id}"))
    assert a.detalhe["motivo"] == "ZZTESTE fim do mandato"
    # admin_lgpd não usa esta rota para outras secretarias
    s = client.post(B + "/vinculos/adicionar", json={"usuario_id": alvo.id, "secretaria": "SAUDE"}, headers=tokens["admin"]).json()
    r = client.post(B + "/vinculos/remover-prefeito", json={"vinculo_id": s["id"], "versao": 1, "motivo": "ZZTESTE motivo longo"},
                    headers=tokens["admin_lgpd"])
    assert r.status_code == 422


# ---------- QA #50 (b31722c): secretaria DONA, rotas cobertas, revelar por função, TODAS imutável ----------
def _so(db, client, sec, perfil="leitura"):
    u, h = _usuario(db, client, perfil)
    _vinculo(db, u, sec)
    return u, h


@pytest.mark.parametrize("rota", ["", "/linha-do-tempo", "/sistemas", "/acessos"])
def test_qa1_outra_secretaria_nao_ve_ficha_linha_sistemas(client, db, rbac, rota):
    _u, h = _so(db, client, "ADMINISTRACAO", "admin")  # dono só de "sei" (a confirmar)
    cid = _cidadao(db)
    r = client.get(f"/api/gold/cidadaos/{cid}{rota}", headers=h)
    assert r.status_code == 403 and "ZZTESTE" not in r.text


def test_qa1_secretaria_sem_dominio_nao_lista_nada(client, db, rbac):
    _cidadao(db)
    _u, h = _so(db, client, "ASSISTENCIA_SOCIAL")  # não é dona de nenhum domínio
    for rota in ("/api/silver/cidadaos", "/api/silver/cidadaos.csv"):
        r = client.get(rota, headers=h)
        assert r.status_code == 403 and "ZZTESTE" not in r.text


def test_qa1_listas_filtram_pela_dona_e_menores(client, db, rbac):
    adulto = _cidadao(db, dominios=["saude"])
    menor = _cidadao(db, nasc=HOJE - timedelta(days=365 * 8), dominios=["saude", "educacao"])
    so_trib = _cidadao(db, dominios=["tributario"])
    _u, hs = _so(db, client, "SAUDE")
    ids = {x["cidadao_id"] for x in client.get("/api/silver/cidadaos?limite=200&busca=ZZTESTE", headers=hs).json()}
    assert adulto in ids and menor not in ids and so_trib not in ids
    csv = client.get("/api/silver/cidadaos.csv", headers=hs).text
    assert menor not in csv and so_trib not in csv
    _u, he = _so(db, client, "EDUCACAO")
    ids = {x["cidadao_id"] for x in client.get("/api/silver/cidadaos?limite=200&busca=ZZTESTE", headers=he).json()}
    assert menor in ids and adulto not in ids
    linha = next(x for x in client.get("/api/silver/cidadaos?limite=200&busca=ZZTESTE", headers=he).json() if x["cidadao_id"] == menor)
    assert linha["dominios"] == ["educacao"]


def test_qa1_ficha_e_linha_do_tempo_so_dominios_da_dona(client, db, rbac):
    from app.models.camadas import Evento
    cid = _cidadao(db)
    db.add(Evento(cidadao_id=cid, data=HOJE, area="saude", tipo="consulta", titulo="ZZTESTE consulta", fonte="ZZ"))
    db.add(Evento(cidadao_id=cid, data=HOJE, area="impostos", tipo="pagamento", titulo="ZZTESTE pagamento", fonte="ZZ"))
    db.commit()
    _u, h = _so(db, client, "FAZENDA")
    f = client.get(f"/api/gold/cidadaos/{cid}", headers=h).json()
    assert {d["dominio"] for d in f["dados"]} == {"tributario"} and f["dominios"] == ["tributario"] and f["sistemas"] == []
    ev = client.get(f"/api/gold/cidadaos/{cid}/linha-do-tempo", headers=h).json()["eventos"]
    assert [e["area"] for e in ev] == ["impostos"]
    s2 = client.get(f"/api/gold/cidadaos/{cid}/sistemas", headers=h).json()
    assert s2["dominios"] == ["tributario"] and s2["sistemas"] == []


def test_qa2_rotas_de_qualidade_municipes_e_acessos(client, db, rbac, tokens):
    _u, hf = _so(db, client, "FAZENDA", "admin")
    assert client.get("/api/silver/municipes", headers=hf).status_code == 200
    for rota in ("/api/silver/pendencias", "/api/silver/inconsistencias"):
        assert client.get(rota, headers=hf).status_code == 403
    _u, hs = _so(db, client, "SAUDE", "admin")
    for rota in ("/api/silver/municipes", "/api/silver/municipes/pendencias"):
        assert client.get(rota, headers=hs).status_code == 403
    cid = _cidadao(db)
    assert client.get(f"/api/gold/cidadaos/{cid}/acessos", headers=tokens["admin"]).status_code == 403  # admin sem vínculo
    assert client.get(f"/api/gold/cidadaos/{cid}/acessos", headers=hs).status_code == 200


# Classificação de TODAS as rotas do app. Rota nova sem classificação = teste falha (QA #50 item 2).
PESSOAIS = {
    ("GET", "/api/silver/cidadaos"), ("GET", "/api/silver/cidadaos.csv"), ("GET", "/api/silver/pendencias"),
    ("GET", "/api/silver/municipes"), ("GET", "/api/silver/municipes/pendencias"), ("GET", "/api/silver/inconsistencias"),
    ("GET", "/api/gold/cidadaos/{cid}"), ("GET", "/api/gold/cidadaos/{cid}/tributario"),
    ("GET", "/api/gold/cidadaos/{cid}/linha-do-tempo"), ("GET", "/api/gold/cidadaos/{cid}/saude"),
    ("GET", "/api/gold/cidadaos/{cid}/acessos"), ("GET", "/api/vinculos/{no_id}"), ("GET", "/api/gold/cidadaos/{cid}/sistemas"),
    ("POST", "/api/silver/pendencias/{pid}/confirmar-obito"), ("GET", "/api/gold/cidadaos/{cid}/educacao"),
    ("POST", "/api/explorar/conectores"), ("POST", "/api/explorar/tabelas"), ("POST", "/api/explorar/perfil"),
    ("POST", "/api/explorar/amostra"), ("POST", "/api/explorar/funil"), ("POST", "/api/explorar/linhagem"),
    ("POST", "/api/explorar/revelar"), ("POST", "/api/sei/interessados"), ("POST", "/api/sei/interessados/revelar"),
    ("GET", "/api/contagens/{eid}/cpfs-repetidos"),
}
# Revelação por FUNÇÃO (admin_lgpd), sem vínculo, com motivo obrigatório e auditoria (TL 29/10).
POR_FUNCAO = {
    ("POST", "/api/cidadaos/{cid}/revelar-cpf"), ("POST", "/api/gold/cidadaos/{cid}/educacao/revelar"),
    ("POST", "/api/gold/cidadaos/{cid}/tributario/revelar-empresas"),
}
# admin_lgpd entra por função nestas, com motivo no cabeçalho (TL 29/10); os demais seguem a secretaria dona (prefeito).
POR_FUNCAO_COM_MOTIVO = {
    ("GET", "/api/silver/pendencias"), ("GET", "/api/silver/inconsistencias"),
    ("POST", "/api/silver/pendencias/{pid}/confirmar-obito"), ("GET", "/api/contagens/{eid}/cpfs-repetidos"),
} | {("POST", "/api/explorar/" + r) for r in ("conectores", "tabelas", "perfil", "amostra", "funil", "linhagem", "revelar")}
NAO_PESSOAIS = {  # operação, catálogo, agregados, auditoria técnica e a própria tela de TI (só admin)
    ("GET", "/api/auth/me"), ("GET", "/api/dashboard"), ("GET", "/api/catalogo/sistemas"), ("POST", "/api/catalogo/sistemas"),
    ("PUT", "/api/catalogo/sistemas/{sid}"), ("POST", "/api/catalogo/sistemas/{sid}/tabelas"), ("GET", "/api/conectores"),
    ("POST", "/api/conectores/{conector}/testar"), ("POST", "/api/conectores/demo/executar"), ("POST", "/api/conectores/sil/amostra"),
    ("GET", "/api/conectores/sil/amostra/{eid}"), ("POST", "/api/conectores/{conector}/amostra"),
    ("GET", "/api/conectores/{conector}/amostra/{eid}"), ("GET", "/api/conectores/sil/status"),
    ("GET", "/api/conectores/{conector}/bloqueio"), ("POST", "/api/conectores/{conector}/execucoes/{eid}/interromper"),
    ("POST", "/api/conectores/{base_id:int}/executar"), ("POST", "/api/conectores/{conector}/diagnostico"),
    ("POST", "/api/conectores/diagnostico"), ("GET", "/api/conectores/{conector}/completa"),
    ("GET", "/api/conectores/{conector}/completa/estimativa"), ("POST", "/api/conectores/{conector}/completa"),
    ("GET", "/api/conectores/{conector}/completa/quarentena"), ("POST", "/api/conectores/{conector}/completa/retomar-camadas"),
    ("POST", "/api/conectores/{conector}/completa/cancelar"), ("GET", "/api/sistema/recursos"),
    ("POST", "/api/conectores/iss/municipes/executar"), ("GET", "/api/execucoes/{eid}/logs"), ("GET", "/api/indicadores"),
    ("GET", "/api/politicas"), ("POST", "/api/politicas/retencao/executar"), ("POST", "/api/usuarios"), ("GET", "/api/auditoria"),
    ("GET", "/api/fontes/progresso"), ("GET", "/api/agenda"), ("PUT", "/api/agenda/{conector}"),
    ("POST", "/api/agenda/{conector}/executar-agora"), ("GET", "/api/educacao/releitura-deploy"),
    ("POST", "/api/educacao/conector/cancelar"), ("POST", "/api/educacao/conector/execucoes/{eid:int}/marcar-falha"),
    ("POST", "/api/educacao/conector/{modo}"), ("GET", "/api/health"), ("POST", "/api/auth/login"),
    ("GET", "/api/sei/status"), ("POST", "/api/sei/amostra"), ("POST", "/api/sei/incremental"),
    ("POST", "/api/contagens/executar"), ("GET", "/api/contagens/ultima"),  # PR-07: só agregados
} | {("POST", "/api/ti/acesso-secretaria" + r) for r in (
    "/secretarias", "/referencia/listar", "/referencia/secretaria", "/referencia/dominio", "/usuarios/buscar", "/vinculos/listar",
    "/vinculos/adicionar", "/vinculos/editar", "/vinculos/remover", "/vinculos/historico", "/vinculos/remover-prefeito",
    "/vinculos/conceder-prefeito")}


def _rotas_app():
    from app.api.acesso_ti import ti
    from app.api.rotas import api, publico
    from app.connectors.sei.rotas import sei
    from app.contagens.rotas import contagens
    from app.explorador.rotas import explorador
    return {(m, "/api" + r.path) for R in (api, publico, explorador, sei, ti, contagens) for r in R.routes for m in r.methods - {"HEAD"}}


def test_qa2_toda_rota_do_app_esta_classificada():
    rotas = _rotas_app()
    sem = rotas - PESSOAIS - POR_FUNCAO - NAO_PESSOAIS
    assert not sem, f"Rota sem classificação PR-06 (dado pessoal precisa da regra): {sorted(sem)}"
    assert not (PESSOAIS & NAO_PESSOAIS) and (PESSOAIS | POR_FUNCAO) <= rotas


@pytest.mark.parametrize("perfil", ["admin", "admin_lgpd"])
def test_qa2_toda_rota_pessoal_aplica_a_regra(client, db, rbac, perfil):
    """Usuário com perfil alto mas vínculo só com secretaria que não é dona de nada: toda rota pessoal = 403."""
    _u, h = _so(db, client, "ASSISTENCIA_SOCIAL", perfil)
    cid = _cidadao(db)
    for m, path in sorted(PESSOAIS):
        url = path.replace("{cid}", cid).replace("{pid}", "1").replace("{no_id}", f"pf:{cid}")
        url += "?motivo=ZZTESTE+motivo+longo" if "vinculos" in url else ""
        r = client.request(m, url, headers=h, json={})
        esperado = 400 if perfil == "admin_lgpd" and (m, path) in POR_FUNCAO_COM_MOTIVO else 403  # sem motivo
        assert r.status_code == esperado, (m, path, r.status_code)
        assert "ZZTESTE" not in r.text


def test_qa3_admin_lgpd_revela_cpf_sem_vinculo_com_motivo_e_auditoria(client, db, rbac, tokens):
    cid = _cidadao(db)
    _cpf(db, cid, "86288366757")
    h = tokens["admin_lgpd"]
    r = client.post(f"/api/cidadaos/{cid}/revelar-cpf", json={}, headers=h)
    assert r.status_code == 400 and "86288366757" not in r.text
    db.expire_all()
    assert db.scalar(select(Auditoria).where(Auditoria.acao == "revelar_cpf_negado", Auditoria.alvo == f"cidadao:{cid}")) is not None
    r = client.post(f"/api/cidadaos/{cid}/revelar-cpf", json={"motivo": "ZZTESTE ofício 123/2026"}, headers=h)
    assert r.status_code == 200 and r.json()["cpf"] == "86288366757"
    db.expire_all()
    a = db.scalar(select(Auditoria).where(Auditoria.acao == "revelar_cpf", Auditoria.alvo == f"cidadao:{cid}"))
    assert a.detalhe["motivo"] == "ZZTESTE ofício 123/2026" and a.usuario == "admin_lgpd@aion.local"
    for perfil in ("admin", "operador", "leitura"):
        assert client.post(f"/api/cidadaos/{cid}/revelar-cpf", json={"motivo": "ZZTESTE motivo longo"},
                           headers=tokens[perfil]).status_code == 403


def test_qa4_todas_nao_e_editado_nem_encerrado(client, db, tokens):
    alvo, _ = _usuario(db, client)
    v = client.post(B + "/vinculos/conceder-prefeito", json={"usuario_id": alvo.id, "motivo": "ZZTESTE posse do prefeito"}, headers=tokens["admin_lgpd"]).json()
    ontem = (HOJE - timedelta(days=1)).isoformat()
    for corpo in ({"vinculo_id": v["id"], "versao": 1, "inicio": "2026-01-01", "fim": ontem},
                  {"vinculo_id": v["id"], "versao": 1, "inicio": (HOJE + timedelta(days=5)).isoformat()}):
        assert client.post(B + "/vinculos/editar", json=corpo, headers=tokens["admin"]).status_code == 422
        assert client.post(B + "/vinculos/editar", json=corpo, headers=tokens["admin_lgpd"]).status_code == 403
    db.expire_all()
    linha = db.get(UsuarioSecretaria, v["id"])
    assert linha.vigencia_fim is None and linha.removido_em is None and linha.versao == 1


# ---------- TL 29/10 18h23: pendências, explorador e óbito = prefeito; admin_lgpd por função com motivo ----------
MOT = {"X-SIGPMF-Motivo": "ZZTESTE auditoria de qualidade"}


@pytest.mark.parametrize("m,url", [("GET", "/api/silver/pendencias"), ("GET", "/api/silver/inconsistencias"),
                                   ("POST", "/api/explorar/conectores")])
def test_tl_admin_lgpd_por_funcao_exige_motivo_e_audita(client, db, rbac, tokens, m, url):
    h = tokens["admin_lgpd"]  # sem vínculo nenhum
    r = client.request(m, url, headers=h, json={})
    assert r.status_code == 400
    db.expire_all()
    neg = db.scalar(select(Auditoria).where(Auditoria.acao == "acesso_por_funcao_negado").order_by(Auditoria.id.desc()))
    assert neg.usuario == "admin_lgpd@aion.local" and neg.detalhe == {"razao": "motivo_invalido"}
    curto = client.request(m, url, headers={**h, "X-SIGPMF-Motivo": "curto"}, json={})
    assert curto.status_code == 400
    ok = client.request(m, url, headers={**h, **MOT}, json={})
    assert ok.status_code == 200, ok.text
    db.expire_all()
    a = db.scalar(select(Auditoria).where(Auditoria.acao == "acesso_por_funcao").order_by(Auditoria.id.desc()))
    assert a.detalhe["motivo"] == MOT["X-SIGPMF-Motivo"] and a.usuario == "admin_lgpd@aion.local"
    aa = db.scalar(select(AuditoriaAcesso).where(AuditoriaAcesso.usuario == "admin_lgpd@aion.local")
                   .order_by(AuditoriaAcesso.id.desc()))
    assert aa.secretaria == "FUNCAO_ADMIN_LGPD"


def test_tl_confirmar_obito_admin_lgpd_por_funcao(client, db, rbac, tokens):
    url = "/api/silver/pendencias/999999/confirmar-obito"
    corpo = {"cidadao_id": str(uuid.uuid4()), "motivo": "ZZTESTE certidão conferida"}
    assert client.post(url, headers=tokens["admin_lgpd"], json=corpo).status_code == 400
    r = client.post(url, headers={**tokens["admin_lgpd"], **MOT}, json=corpo)
    assert r.status_code not in (400, 403)  # passou da regra; pendência inexistente = 404 da própria rota


@pytest.mark.parametrize("url", ["/api/silver/pendencias", "/api/silver/inconsistencias"])
def test_tl_admin_sem_vinculo_perde_e_prefeito_ve(client, db, rbac, tokens, url):
    assert client.get(url, headers={**tokens["admin"], **MOT}).status_code == 403  # admin não entra por função
    _u, hp = _so(db, client, "TODAS", "leitura")
    assert client.get(url, headers=hp).status_code == 200


def test_tl_motivo_por_funcao_so_com_regra_ligada(client, tokens, monkeypatch):
    monkeypatch.delenv("SIGPMF_RBAC_SECRETARIA", raising=False)
    assert client.get("/api/silver/pendencias", headers=tokens["admin_lgpd"]).status_code == 200


# ---------- QA #50 ciclo 2 + adendo: autoconcessão, TODAS por fluxo próprio, concessão cruzada ----------
def _neg(db, acao, razao):
    db.expire_all()
    return db.scalar(select(Auditoria).where(Auditoria.acao == acao, Auditoria.detalhe["razao"].as_string() == razao)
                     .order_by(Auditoria.id.desc()))


def _id(db, email):
    db.expire_all()
    return db.scalar(select(Usuario.id).where(Usuario.email == email))


def test_c2_admin_nao_concede_todas_nem_a_si_mesmo(client, db, rbac, tokens):
    eu = _id(db, "admin@aion.local")
    cid = _cidadao(db)
    for sec in ("TODAS", "SAUDE"):
        r = client.post(B + "/vinculos/adicionar", json={"usuario_id": eu, "secretaria": sec}, headers=tokens["admin"])
        assert r.status_code == 403
    assert _neg(db, "vinculo_secretaria_recusado", "auto_concessao") is not None
    assert client.get(f"/api/gold/cidadaos/{cid}", headers=tokens["admin"]).status_code == 403  # repro do Tester
    alvo, _ = _usuario(db, client)
    ontem = (HOJE - timedelta(days=1)).isoformat()
    for corpo in ({"usuario_id": alvo.id, "secretaria": "TODAS"}, {"usuario_id": alvo.id, "secretaria": "TODAS", "fim": ontem}):
        assert client.post(B + "/vinculos/adicionar", json=corpo, headers=tokens["admin"]).status_code == 422
    assert _neg(db, "vinculo_secretaria_recusado", "todas_fluxo_proprio") is not None
    db.expire_all()
    assert db.scalar(select(UsuarioSecretaria).where(UsuarioSecretaria.secretaria == "TODAS",
                                                     UsuarioSecretaria.usuario_id.in_([eu, alvo.id]))) is None


def test_c2_conceder_prefeito_so_admin_lgpd_com_motivo_sem_fim(client, db, tokens):
    alvo, _ = _usuario(db, client)
    corpo = {"usuario_id": alvo.id, "motivo": "ZZTESTE posse do prefeito"}
    for perfil in ("admin", "operador", "leitura"):
        assert client.post(B + "/vinculos/conceder-prefeito", json=corpo, headers=tokens[perfil]).status_code == 403
    assert client.post(B + "/vinculos/conceder-prefeito", json={**corpo, "motivo": "curto"},
                       headers=tokens["admin_lgpd"]).status_code == 422
    assert _neg(db, "vinculo_prefeito_conceder_recusado", "motivo_invalido") is not None
    r = client.post(B + "/vinculos/conceder-prefeito", json={**corpo, "fim": "2026-01-01"}, headers=tokens["admin_lgpd"])
    assert r.status_code == 201 and r.json()["fim"] is None and r.json()["prefeito"] is True  # `fim` é ignorado
    db.expire_all()
    a = db.scalar(select(Auditoria).where(Auditoria.acao == "vinculo_prefeito_conceder", Auditoria.alvo == f"usuario:{alvo.id}"))
    assert a.detalhe["motivo"] == "ZZTESTE posse do prefeito" and a.usuario == "admin_lgpd@aion.local"
    assert client.post(B + "/vinculos/conceder-prefeito", json=corpo, headers=tokens["admin_lgpd"]).status_code == 409


def test_c2_conceder_prefeito_recusa_a_si_e_a_contas_admin(client, db, tokens):
    eu = _id(db, "admin_lgpd@aion.local")
    r = client.post(B + "/vinculos/conceder-prefeito", json={"usuario_id": eu, "motivo": "ZZTESTE motivo longo"},
                    headers=tokens["admin_lgpd"])
    assert r.status_code == 403 and _neg(db, "vinculo_prefeito_conceder_recusado", "auto_concessao") is not None
    for perfil in ("admin", "admin_lgpd"):
        alvo, _ = _usuario(db, client, perfil)
        r = client.post(B + "/vinculos/conceder-prefeito", json={"usuario_id": alvo.id, "motivo": "ZZTESTE motivo longo"},
                        headers=tokens["admin_lgpd"])
        assert r.status_code == 422
    assert _neg(db, "vinculo_prefeito_conceder_recusado", "perfil_administrativo") is not None


def test_c2_concessao_cruzada_recusada(client, db):
    """A (admin_lgpd) concede TODAS a B; B nunca devolve TODAS a A. Para o teste, B tem perfil leitura ao receber e
    depois vira admin_lgpd (troca de perfil fora da tela) e tenta conceder de volta."""
    a, ha = _usuario(db, client, "admin_lgpd")
    b, _hb = _usuario(db, client, "leitura")
    r = client.post(B + "/vinculos/conceder-prefeito", json={"usuario_id": b.id, "motivo": "ZZTESTE posse do prefeito"}, headers=ha)
    assert r.status_code == 201
    # mesmo sem a regra de perfil, a devolução é recusada: simula um TODAS de A concedido por B
    db.add(UsuarioSecretaria(usuario_id=a.id, secretaria="TODAS", vigencia_inicio=HOJE, cadastrado_por=b.email, removido_em=None))
    db.commit()
    c, hc = _usuario(db, client, "admin_lgpd")
    db.add(UsuarioSecretaria(usuario_id=c.id, secretaria="TODAS", vigencia_inicio=HOJE - timedelta(days=1),
                             cadastrado_por=b.email))
    db.commit()
    alvo_b = db.get(Usuario, b.id)
    alvo_b.perfil = "leitura"
    db.commit()
    # C recebeu TODAS de B; C tenta conceder TODAS a B (que já tem) -> recusa cruzada antes do 409
    r = client.post(B + "/vinculos/conceder-prefeito", json={"usuario_id": b.id, "motivo": "ZZTESTE devolução"}, headers=hc)
    assert r.status_code == 403 and "concedeu acesso de prefeito a você" in r.json()["detail"]
    assert _neg(db, "vinculo_prefeito_conceder_recusado", "concessao_cruzada") is not None


def test_c2_referencia_nao_altera_todas_nem_aponta_dominio_para_todas(client, tokens):
    h = tokens["admin"]
    r = client.post(B + "/referencia/secretaria", json={"codigo": "TODAS", "nome": "ZZTESTE renomeado"}, headers=h)
    assert r.status_code == 422
    r = client.post(B + "/referencia/dominio", json={"dominio": "tributario", "secretaria": "TODAS"}, headers=h)
    assert r.status_code == 422
    nomes = {x["codigo"]: x["nome"] for x in client.post(B + "/referencia/listar", json={}, headers=h).json()["secretarias"]}
    assert nomes["TODAS"] == "Todas as secretarias (prefeito)"


def test_c2_menor_visivel_pela_educacao(client, db, rbac):
    cid = _cidadao(db, nasc=HOJE - timedelta(days=365 * 9), dominios=["educacao", "saude"])
    db.add(DadoGold(cidadao_id=cid, dominio="educacao", campo="escola", valor="ZZTESTE ESCOLA", sistema="ZZ"))
    db.commit()
    _cpf(db, cid, "15350946056")
    u, h = _so(db, client, "EDUCACAO")
    r = client.get(f"/api/gold/cidadaos/{cid}", headers=h)
    assert r.status_code == 200
    f = r.json()
    assert {d["dominio"] for d in f["dados"]} == {"educacao"} and f["cpf"] == "15350946056"
    ids = {x["cidadao_id"] for x in client.get("/api/silver/cidadaos?limite=200&busca=ZZTESTE", headers=h).json()}
    assert cid in ids
    db.expire_all()
    a = db.scalar(select(AuditoriaAcesso).where(AuditoriaAcesso.usuario == u.email, AuditoriaAcesso.recurso == "ficha"))
    assert a.secretaria == "EDUCACAO" and a.revelou_cpf is True


def test_c2_cpfs_repetidos_admin_lgpd_exige_motivo(client, rbac, tokens):
    r = client.get("/api/contagens/999999/cpfs-repetidos", headers=tokens["admin_lgpd"])
    assert r.status_code == 400
    r = client.get("/api/contagens/999999/cpfs-repetidos", headers={**tokens["admin_lgpd"], **MOT})
    assert r.status_code == 404  # passou da regra; contagem inexistente
