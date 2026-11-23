"""Integração HTTP isolada: sessão JWT real + banco SQLite apenas para testar o módulo.

A migração e o deploy continuam PostgreSQL. Não altera DATABASE_URL de produção.
"""
import os
import unittest
from datetime import date

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite://")
os.environ.setdefault("JWT_SECRET", "somente-teste-plano-governo-2026")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.core.db import get_db  # noqa: E402
from app.core.security import cria_token  # noqa: E402
from app.models.base import Base  # noqa: E402
from app.models.controle import Auditoria, SecretariaRef, Usuario, UsuarioSecretaria  # noqa: E402
from app.plano_governo.modelo import PlanoGovernoEstado  # noqa: E402
from app.plano_governo.rotas import plano  # noqa: E402


class TestPlanoAPI(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite+pysqlite://", connect_args={"check_same_thread": False},
                                    poolclass=StaticPool, execution_options={"schema_translate_map": {"controle": None}})
        Base.metadata.create_all(self.engine, tables=[x.__table__ for x in (
            Usuario, SecretariaRef, UsuarioSecretaria, Auditoria, PlanoGovernoEstado)])
        db = Session(self.engine, expire_on_commit=False)
        self.tokens = {}
        for nome, perfil in (("admin", "admin"), ("edu", "operador"), ("saude", "operador"), ("sem", "leitura"),
                             ("gabinete", "leitura")):
            u = Usuario(email=f"{nome}@teste.local", nome=nome, perfil=perfil, senha_hash="somente-teste", ativo=True)
            db.add(u)
            db.flush()
            self.tokens[nome] = {"Authorization": f"Bearer {cria_token(u)}"}
            if nome in ("edu", "saude", "gabinete"):
                db.add(UsuarioSecretaria(usuario_id=u.id, secretaria={"edu": "EDUCACAO", "saude": "SAUDE",
                           "gabinete": "TODAS"}[nome], vigencia_inicio=date(2020, 1, 1), cadastrado_por="teste"))
        db.add_all([SecretariaRef(codigo="EDUCACAO", nome="Educação"), SecretariaRef(codigo="SAUDE", nome="Saúde")])
        db.commit()
        db.close()
        app = FastAPI()
        app.include_router(plano, prefix="/api")

        def teste_db():
            with Session(self.engine, expire_on_commit=False) as sessao:
                yield sessao
        app.dependency_overrides[get_db] = teste_db
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.engine.dispose()

    def get(self, perfil="admin"):
        return self.client.get("/api/plano-governo/state", headers=self.tokens[perfil])

    def post(self, acao, dados, versao, perfil="admin"):
        return self.client.post("/api/plano-governo/action", headers=self.tokens[perfil],
                                json={"action": acao, "data": dados, "version": versao})

    def test_jwt_e_vinculo_sao_obrigatorios(self):
        self.assertEqual(self.client.get("/api/plano-governo/state").status_code, 401)
        self.assertEqual(self.get("sem").status_code, 403)
        self.assertEqual(self.get("gabinete").json()["user"]["role"], "leitura")

    def test_gravacao_persistencia_auditoria_e_conflito(self):
        inicial = self.get().json()
        self.assertEqual(len(inicial["state"]["commitments"]), 70)
        dados = {"title": "Laboratório", "departmentId": "EDUCACAO", "commitmentIds": [13],
                 "status": "em_andamento", "progress": 10}
        r = self.post("project.save", dados, inicial["version"])
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.post("project.save", dados, inicial["version"]).status_code, 409)
        nova = self.get().json()
        self.assertEqual(len(nova["state"]["projects"]), 50)
        self.assertEqual(nova["version"], 1)
        with Session(self.engine) as db:
            self.assertIsNotNone(db.scalar(select(Auditoria).where(Auditoria.acao == "plano_governo_alterado")))

    def test_secretaria_nao_ve_nem_edita_projeto_alheio(self):
        dados = {"title": "Laboratório", "departmentId": "EDUCACAO", "commitmentIds": [13],
                 "status": "em_andamento", "progress": 10}
        self.assertEqual(self.post("project.save", dados, 0).status_code, 200)
        original = next(p for p in self.get().json()["state"]["projects"] if p["title"] == "Laboratório")
        self.assertFalse(any(p["title"] == "Laboratório" for p in self.get("saude").json()["state"]["projects"]))
        self.assertEqual(self.post("project.save", original, 1, "saude").status_code, 403)
        self.assertEqual(self.post("project.save", {**original, "progress": 20}, 1, "edu").status_code, 200)

    def test_usuario_leitura_todas_nao_altera_projetos(self):
        dados = {"title": "Laboratório", "departmentId": "EDUCACAO", "commitmentIds": [13], "status": "planejado"}
        self.assertEqual(self.post("project.save", dados, 0, "gabinete").status_code, 403)

    def test_todas_nao_amplia_perfil_leitura_para_validacao(self):
        self.assertEqual(self.post("commitment.save", {"id": 13, "validation": "parcial"}, 0, "gabinete").status_code, 403)
