"""Fluxo público com dados e transporte SMTP sintéticos; nenhum cadastro real é consultado."""
import os
import unittest
from datetime import date
from unittest.mock import patch

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite://")
os.environ.setdefault("JWT_SECRET", "somente-teste-plano-governo-2026")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.db import get_db  # noqa: E402
from app.core.security import cria_token  # noqa: E402
from app.models.base import Base  # noqa: E402
from app.models.camadas import Cidadao, DadoGold, Identificador, Imovel, ImovelPossuidor, Lancamento  # noqa: E402
from app.models.controle import Auditoria, Usuario  # noqa: E402
from app.portal_cidadao.modelos import AcessoCidadao  # noqa: E402
from app.portal_cidadao.rotas import portal  # noqa: E402
from app.portal_cidadao.servico import COOKIE, conferir, nome_normalizado  # noqa: E402


class TestPortal(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite+pysqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
                            execution_options={"schema_translate_map": {"controle": None, "silver": None, "gold": None}})
        Base.metadata.create_all(self.engine, tables=[x.__table__ for x in (
            AcessoCidadao, Auditoria, Usuario, Cidadao, Identificador, DadoGold, Imovel, ImovelPossuidor, Lancamento)])
        with Session(self.engine) as db:
            db.add(Cidadao(cidadao_id="pessoa-teste", nome="Pessoa Teste", data_nascimento=date(1985, 5, 12), falecido=False))
            db.add(Identificador(cidadao_id="pessoa-teste", tipo="cpf", valor="52998224725"))
            for campo, valor in (("nome_mae", "Mãe de Teste"), ("email", "teste@example.invalid"), ("diagnostico", "NAO EXPOR")):
                db.add(DadoGold(cidadao_id="pessoa-teste", dominio="identificacao", campo=campo, valor=valor,
                                sistema="TESTE", mais_recente=True))
            db.add(Lancamento(id=1, chave_origem="exemplo-1", exercicio=2026, tributo="IPTU", parcela=1,
                   cidadao_id="pessoa-teste", valor=100, pago=False))
            db.add(Lancamento(id=2, chave_origem="outro-cidadao", exercicio=2026, tributo="IPTU", parcela=1,
                   cidadao_id="outra-pessoa", valor=999, pago=False))
            db.commit()
        app = FastAPI()
        app.include_router(portal, prefix="/api")

        def banco():
            with Session(self.engine) as db:
                yield db
        app.dependency_overrides[get_db] = banco
        self.client = TestClient(app)
        self.smtp = patch("app.portal_cidadao.servico.enviar_codigo", return_value=True).start()
        self.pronto = patch("app.portal_cidadao.rotas.pronto", return_value=True).start()

    def tearDown(self):
        patch.stopall()
        self.client.close()
        self.engine.dispose()

    def identificar(self, **extra):
        return self.client.post("/api/portal/identificar", json={"cpf": "52998224725", "nascimento": "1985-05-12",
                                                          "nome_mae": "Mae de Teste", **extra})

    def login(self):
        r = self.identificar()
        codigo = self.smtp.call_args.args[1]
        return self.client.post("/api/portal/confirmar", json={"desafio": r.json()["desafio"], "codigo": codigo})

    def test_normalizacao_nao_faz_correspondencia_aproximada(self):
        self.assertEqual(nome_normalizado("  MÃE  de Teste "), "mae de teste")
        with Session(self.engine) as db:
            self.assertEqual(conferir(db, "52998224725", date(1985, 5, 12), "mãe de teste")[0], "pessoa-teste")
            self.assertEqual(conferir(db, "52998224725", date(1985, 5, 12), "mae"), (None, None))

    def test_cadastro_correto_e_errado_tem_mesma_resposta_sem_revelacao(self):
        a = self.identificar().json()
        b = self.identificar(nome_mae="Pessoa errada").json()
        self.assertEqual(a["mensagem"], b["mensagem"])
        self.assertEqual(set(a), set(b))
        self.assertNotIn("pessoa-teste", str(a))
        self.assertEqual(self.smtp.call_count, 1)
        self.assertEqual(self.client.get("/api/portal/me").status_code, 401)

    def test_otp_uso_unico_sessao_separada_e_consulta_propria(self):
        r = self.identificar().json()
        codigo = self.smtp.call_args.args[1]
        dados = {"desafio": r["desafio"], "codigo": codigo}
        resp = self.client.post("/api/portal/confirmar", json=dados)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("HttpOnly", resp.headers["set-cookie"])
        self.assertIn("SameSite=strict", resp.headers["set-cookie"])
        self.assertEqual(self.client.post("/api/portal/confirmar", json=dados).status_code, 401)
        me = self.client.get("/api/portal/me")
        self.assertEqual(me.status_code, 200)
        self.assertEqual(me.headers["cache-control"], "no-store")
        self.assertEqual(len(me.json()["lancamentos"]), 1)
        self.assertNotIn("52998224725", me.text)
        self.assertNotIn("NAO EXPOR", me.text)
        self.assertEqual(me.json()["totais"]["em_aberto"], 100)
        self.assertEqual(self.client.post("/api/portal/sair").status_code, 403)
        self.assertEqual(self.client.post("/api/portal/sair", headers={"X-Portal-CSRF": me.json()["csrf"]}).status_code, 200)
        self.assertEqual(self.client.get("/api/portal/me").status_code, 401)
        with Session(self.engine) as db:
            self.assertIsNotNone(db.scalar(select(Auditoria).where(Auditoria.acao == "portal_acesso")))

    def test_codigo_errado_bloqueia_apos_cinco_tentativas(self):
        r = self.identificar().json()
        codigo = self.smtp.call_args.args[1]
        errado = "000000" if codigo != "000000" else "111111"
        for _ in range(5):
            self.assertEqual(self.client.post("/api/portal/confirmar", json={"desafio": r["desafio"], "codigo": errado}).status_code, 401)
        self.assertEqual(self.client.post("/api/portal/confirmar", json={"desafio": r["desafio"], "codigo": codigo}).status_code, 401)

    def test_limite_cpf_sem_guardar_respostas(self):
        for _ in range(6):
            self.assertEqual(self.identificar(nome_mae="Pessoa errada").status_code, 200)
        self.assertEqual(self.identificar().status_code, 429)
        with Session(self.engine) as db:
            rows = list(db.scalars(select(AcessoCidadao)))
            self.assertFalse(any("52998224725" in x.chave or "Mae" in (x.segredo_hash or "") for x in rows))

    def test_guia_exige_sessao_csrf_propriedade_e_integracao_real(self):
        self.assertEqual(self.client.post("/api/portal/guias", json={"tipo": "lancamento", "id": "1"}).status_code, 401)
        self.login()
        me = self.client.get("/api/portal/me").json()
        h = {"X-Portal-CSRF": me["csrf"]}
        self.assertEqual(self.client.post("/api/portal/guias", json={"tipo": "lancamento", "id": "1"}).status_code, 403)
        self.assertEqual(self.client.post("/api/portal/guias", headers=h, json={"tipo": "lancamento", "id": "2"}).status_code, 404)
        with patch.object(get_settings(), "portal_tributos_url", None):
            self.assertEqual(self.client.post("/api/portal/guias", headers=h, json={"tipo": "lancamento", "id": "1"}).status_code, 503)
        with patch("app.portal_cidadao.rotas.integracao", return_value={"url": "https://fraude.example/guia"}):
            self.assertEqual(self.client.post("/api/portal/guias", headers=h, json={"tipo": "lancamento", "id": "1"}).status_code, 502)

    def test_token_interno_nao_serve_como_sessao_de_cidadao(self):
        self.client.cookies.set(COOKIE, "token-de-servidor")
        self.assertEqual(self.client.get("/api/portal/me", headers={"Authorization": "Bearer interno"}).status_code, 401)

    def test_desafio_e_sessao_expirados_sao_recusados(self):
        from datetime import timedelta

        from app.portal_cidadao.servico import agora
        r = self.identificar().json()
        codigo = self.smtp.call_args.args[1]
        with Session(self.engine) as db:
            desafio = db.scalar(select(AcessoCidadao).where(AcessoCidadao.tipo == "desafio"))
            desafio.expira_em = agora() - timedelta(seconds=1)
            db.commit()
        self.assertEqual(self.client.post("/api/portal/confirmar", json={"desafio": r["desafio"], "codigo": codigo}).status_code, 401)
        self.login()
        with Session(self.engine) as db:
            sessao = db.scalar(select(AcessoCidadao).where(AcessoCidadao.tipo == "sessao"))
            sessao.expira_em = agora() - timedelta(seconds=1)
            db.commit()
        self.assertEqual(self.client.get("/api/portal/me").status_code, 401)

    def token_interno(self, perfil):
        with Session(self.engine, expire_on_commit=False) as db:
            u = Usuario(email=perfil + "@teste.local", nome="Teste", perfil=perfil, senha_hash="somente-teste", ativo=True)
            db.add(u)
            db.commit()
            return {"Authorization": "Bearer " + cria_token(u)}

    def test_acesso_teste_exige_admin_e_funciona_sem_email_ou_smtp(self):
        dados = {"cpf": "52998224725", "nascimento": "1985-05-12", "nome_mae": "Mae de Teste"}
        self.assertEqual(self.client.post("/api/portal/acesso-teste", json=dados).status_code, 401)
        self.assertEqual(self.client.post("/api/portal/acesso-teste", json=dados,
                         headers=self.token_interno("operador")).status_code, 403)
        with Session(self.engine) as db:
            email = db.scalar(select(DadoGold).where(DadoGold.campo == "email"))
            db.delete(email)
            db.commit()
        with patch("app.portal_cidadao.rotas.pronto", return_value=False):
            self.assertEqual(self.identificar().status_code, 503)
            r = self.client.post("/api/portal/acesso-teste", json=dados, headers=self.token_interno("admin"))
        self.assertEqual(r.status_code, 200, r.text)
        me = self.client.get("/api/portal/me")
        self.assertEqual(me.status_code, 200)
        self.assertTrue(me.json()["modo_teste"])
        self.assertEqual(self.smtp.call_count, 0)
        with Session(self.engine) as db:
            aud = db.scalar(select(Auditoria).where(Auditoria.acao == "portal_acesso_teste"))
            self.assertEqual(aud.usuario, "admin@teste.local")

    def test_admin_teste_nao_dispensa_conferencia_dos_tres_dados(self):
        h = self.token_interno("admin")
        for dados in ({"nome_mae": "Outra pessoa"}, {"nascimento": "1985-05-13"}):
            body = {"cpf": "52998224725", "nascimento": "1985-05-12", "nome_mae": "Mae de Teste", **dados}
            self.assertEqual(self.client.post("/api/portal/acesso-teste", json=body, headers=h).status_code, 422)
        self.assertEqual(self.client.get("/api/portal/me").status_code, 401)
