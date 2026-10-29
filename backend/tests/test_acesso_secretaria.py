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


def _cidadao(db, nasc=date(1980, 1, 1)):
    cid = str(uuid.uuid4())
    db.add(Cidadao(cidadao_id=cid, nome="ZZTESTE CIDADAO", data_nascimento=nasc))
    db.add(CidadaoSistemas(cidadao_id=cid, nome="ZZTESTE CIDADAO", sistemas=["ZZ"], dominios=["saude", "tributario"]))
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
    assert al["cpf"] != "00000000191"  # CPF segue mascarado (só admin_lgpd revela)


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
def test_r4_menor_so_educacao_e_saude(client, db, rbac):
    cid = _cidadao(db, nasc=HOJE - timedelta(days=365 * 12))
    fz, hf = _usuario(db, client)
    _vinculo(db, fz, "FAZENDA")
    assert client.get(f"/api/gold/cidadaos/{cid}", headers=hf).status_code == 403
    assert client.get(f"/api/gold/cidadaos/{cid}/tributario", headers=hf).status_code == 403
    sa, hs = _usuario(db, client)
    _vinculo(db, sa, "SAUDE")
    assert client.get(f"/api/gold/cidadaos/{cid}", headers=hs).status_code == 200
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
    v = client.post(B + "/vinculos/adicionar", json={"usuario_id": alvo.id, "secretaria": "TODAS"}, headers=tokens["admin"]).json()
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
    assert len(rotas) == 7 and all("{" not in r.path and r.methods == {"POST"} for r in rotas)


def test_auditoria_acesso_somente_insercao(db, client, rbac):
    u, h = _usuario(db, client)
    _vinculo(db, u, "SAUDE")
    client.get(f"/api/gold/cidadaos/{_cidadao(db)}/saude", headers=h)
    with pytest.raises(Exception):  # noqa: B017
        db.execute(text("DELETE FROM controle.auditoria_acesso"))
    db.rollback()
