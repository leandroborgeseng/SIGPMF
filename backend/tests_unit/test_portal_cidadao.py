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


def _conta(tabela):
    from sqlalchemy import func
    return select(func.count()).select_from(tabela)


class TestFichaPortal(unittest.TestCase):
    """Ficha do cidadão autenticado, com cadastro ZZTESTE e CPF sintético final 801."""

    CPF = "00000002801"
    CPF_OUTRO = "11144477735"
    CPF_PARENTE = "52998224725"
    CID = "zzteste-801"
    OUTRO = "zzteste-outro"
    PARENTE = "zzteste-parente"

    def setUp(self):
        from datetime import UTC, datetime

        from app.models.camadas import (
            Agendamento,
            Atendimento,
            DividaAtiva,
            EmpresaSil,
            Endereco,
            EscolaAluno,
            EscolaMatricula,
            Evento,
            RegistroBronze,
            UnidadeSaude,
            Vacinacao,
            Vinculo,
        )
        from app.models.controle import Execucao
        self.datetime = datetime
        self.UTC = UTC
        self.engine = create_engine(
            "sqlite+pysqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
            execution_options={"schema_translate_map": {
                "controle": None, "silver": None, "gold": None, "bronze": None, "ref": None}})
        tabelas = [AcessoCidadao, Auditoria, Usuario, Cidadao, Identificador, DadoGold, Endereco, Imovel,
                   ImovelPossuidor, Lancamento, DividaAtiva, EmpresaSil, Atendimento, Agendamento, Vacinacao,
                   UnidadeSaude, Evento, EscolaAluno, EscolaMatricula, Vinculo, RegistroBronze, Execucao]
        Base.metadata.create_all(self.engine, tables=[x.__table__ for x in tabelas])
        with Session(self.engine) as db:
            db.add(Cidadao(cidadao_id=self.CID, nome="Pessoa ZZTESTE Oitocentos", data_nascimento=date(1980, 8, 1), falecido=False))
            db.add(Cidadao(cidadao_id=self.OUTRO, nome="Outra Pessoa ZZTESTE", data_nascimento=date(1970, 1, 1), falecido=False))
            db.add(Cidadao(cidadao_id=self.PARENTE, nome="Parente ZZTESTE", data_nascimento=date(1955, 3, 3), falecido=False))
            db.add(Identificador(cidadao_id=self.CID, tipo="cpf", valor=self.CPF))
            db.add(Identificador(cidadao_id=self.CID, tipo="cns", valor="898001111111111"))
            db.add(Identificador(cidadao_id=self.OUTRO, tipo="cpf", valor=self.CPF_OUTRO))
            db.add(Identificador(cidadao_id=self.PARENTE, tipo="cpf", valor=self.CPF_PARENTE))
            for campo, valor in (("nome_mae", "Mae ZZTESTE"), ("nome_pai", "Pai ZZTESTE"), ("sexo", "F"),
                                 ("diagnostico", "NAO EXPOR")):
                db.add(DadoGold(cidadao_id=self.CID, dominio="identificacao", campo=campo, valor=valor,
                                sistema="ZZTESTE", mais_recente=True))
            db.add(Endereco(cidadao_id=self.CID, logradouro="Rua de Teste ZZTESTE, 80, Franca/SP", sistema="ZZTESTE",
                            mais_recente=True))
            db.add(Imovel(codi="ZZTESTE801", inscricao="ZZ-801", numero="80", complemento="Rua Ficticia ZZTESTE, 100"))
            db.add(Imovel(codi="ZZTESTE801H", inscricao="ZZ-801-H", complemento="Rua Antiga ZZTESTE"))
            db.add(Imovel(codi="ZZ-PARENTE", inscricao="ZZ-PAR", complemento="Imovel do parente"))
            db.add(ImovelPossuidor(codi="ZZTESTE801", documento=self.CPF, tipo_pessoa="PF", cidadao_id=self.CID, ativo=True))
            db.add(ImovelPossuidor(codi="ZZTESTE801H", documento=self.CPF, tipo_pessoa="PF", cidadao_id=self.CID, ativo=False))
            db.add(ImovelPossuidor(codi="ZZ-PARENTE", documento=self.CPF_PARENTE, tipo_pessoa="PF", cidadao_id=self.PARENTE, ativo=True))
            db.add(Lancamento(chave_origem="zzteste-lanc-801", exercicio=2026, tributo="IPTU", parcela=1,
                              cidadao_id=self.CID, codi="ZZTESTE801", valor=150, pago=False, vencimento=date(2026, 1, 10)))
            db.add(Lancamento(chave_origem="zzteste-lanc-outro", exercicio=2026, tributo="IPTU", parcela=1,
                              cidadao_id=self.OUTRO, valor=999, pago=False))
            db.add(DividaAtiva(chave_origem="zzteste-div-801", codi="ZZTESTE801", ano=2024, cidadao_id=self.CID,
                               saldo=80, em_aberto=True, extinta=False))
            db.add(DividaAtiva(chave_origem="zzteste-div-outro", codi="ZZ-OUTRO", ano=2024, cidadao_id=self.OUTRO,
                               saldo=50, em_aberto=True, extinta=False))
            db.add(EmpresaSil(ccm=80199, documento=self.CPF_OUTRO, tipo_pessoa="PF", cidadao_id=self.OUTRO,
                              nome="EMPRESA ZZTESTE NAO MOSTRAR"))
            db.add(Vacinacao(chave_origem="zz-vac-outro", cidadao_id=self.OUTRO, vacina="ZZTESTE VACINA ALHEIA"))
            db.add(Atendimento(chave_origem="zz-at-outro", cidadao_id=self.OUTRO))
            db.add(Agendamento(chave_origem="zz-ag-outro", cidadao_id=self.OUTRO, data_consulta=date(2026, 12, 1)))
            db.add(EscolaAluno(alu_id=1, cidadao_id=self.OUTRO, status_vinculo="vinculado", excluido=False, situacao=1,
                               nome="Aluno ZZTESTE NAO MOSTRAR"))
            db.add(Evento(cidadao_id=self.CID, data=date(2026, 1, 15), area="impostos", tipo="lancamento",
                          titulo="IPTU ZZTESTE lancado", fonte="FINANCEIRO"))
            db.add(Evento(cidadao_id=self.CID, data=date(2026, 2, 1), area="sei", tipo="processo",
                          titulo="Processo SEI secreto", fonte="SEI"))
            ex = Execucao(tipo="zzteste", status="ok", usuario="zzteste", iniciado_em=datetime.now(UTC))
            db.add(ex)
            db.flush()
            db.add(RegistroBronze(sistema_origem="CEMITERIO", tabela="Titulares", id_origem="zz-sepultura",
                                  dados={"Titular": "Titular ZZTESTE ALHEIO"}, hash="zzteste-hash", execucao_id=ex.id,
                                  ambiente_origem="producao", extraido_em=datetime.now(UTC)))
            db.commit()
        app = FastAPI()
        app.include_router(portal, prefix="/api")

        def banco():
            with Session(self.engine) as db:
                yield db
        app.dependency_overrides[get_db] = banco
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.engine.dispose()

    def entrar(self, cid):
        from app.portal_cidadao.servico import criar_sessao
        with Session(self.engine) as db:
            token = criar_sessao(db, cid)
            db.commit()
        self.client.cookies.clear()
        self.client.cookies.set(COOKIE, token)

    def contagens(self):
        from app.models.camadas import DividaAtiva, EmpresaSil, Endereco, Evento, Vacinacao
        modelos = [Cidadao, Identificador, DadoGold, Endereco, Imovel, ImovelPossuidor, Lancamento,
                   DividaAtiva, EmpresaSil, Evento, Vacinacao]
        with Session(self.engine) as db:
            return {m.__tablename__: db.scalar(_conta(m)) for m in modelos}

    def test_cadastro_801_tem_endereco_imovel_lancamento_e_divida_e_o_resto_vazio(self):
        from app.identity.cpf import valido
        self.assertTrue(valido(self.CPF))
        self.assertTrue(self.CPF.endswith("801"))
        self.entrar(self.CID)
        antes = self.contagens()
        cad = self.client.get("/api/portal/me/cadastro")
        trib = self.client.get("/api/portal/me/tributario")
        saude = self.client.get("/api/portal/me/saude")
        edu = self.client.get("/api/portal/me/educacao")
        cem = self.client.get("/api/portal/me/cemiterio")
        arv = self.client.get("/api/portal/me/genealogia")
        linha = self.client.get("/api/portal/me/linha-do-tempo")
        fontes = self.client.get("/api/portal/me/fontes")
        for r in (cad, trib, saude, edu, cem, arv, linha, fontes):
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(r.headers["cache-control"], "no-store")
            self.assertNotIn("NAO EXPOR", r.text)
            self.assertNotIn(self.CPF, r.text)
            self.assertNotIn("898001111111111", r.text)
            self.assertNotIn("ALHEIA", r.text)
            self.assertNotIn("NAO MOSTRAR", r.text)
            self.assertNotIn("ALHEIO", r.text)
            self.assertNotIn(self.CPF_PARENTE, r.text)
        corpo = cad.json()
        self.assertEqual(corpo["nome_mae"]["valor"], "Mae ZZTESTE")
        self.assertEqual(corpo["nome_pai"]["valor"], "Pai ZZTESTE")
        self.assertEqual(corpo["sexo"]["valor"], "F")
        self.assertIn("***", corpo["cns"])
        self.assertIn("Rua de Teste ZZTESTE", corpo["enderecos"][0]["valor"])
        self.assertNotIn("diagnostico", corpo)
        t = trib.json()
        self.assertEqual(t["totais"]["imoveis"], 1)
        self.assertEqual(sum(1 for i in t["imoveis"] if i["ativo"]), 1)
        self.assertTrue(any(not i["ativo"] for i in t["imoveis"]))
        self.assertEqual(t["imoveis"][0]["complemento"], "Rua Ficticia ZZTESTE, 100")
        self.assertEqual(len(t["lancamentos"]), 1)
        self.assertEqual(t["lancamentos"][0]["valor"], 150)
        self.assertGreater(t["totais"]["divida_ativa"], 0)
        self.assertTrue(any(d["em_aberto"] for d in t["dividas"]))
        self.assertEqual(t["empresas"], [])
        self.assertEqual(saude.json()["vacinas"], [])
        self.assertEqual(saude.json()["atendimentos"], [])
        self.assertEqual(saude.json()["proximos"], [])
        self.assertNotIn("diagnostico", saude.json())
        self.assertEqual(edu.json()["matriculas"], [])
        self.assertEqual(cem.json()["titular"], [])
        self.assertEqual(cem.json()["sepultamento"], [])
        self.assertNotIn("titulares", cem.json())
        self.assertEqual(arv.json()["estado"], "indisponivel")
        self.assertEqual(arv.json()["parentes"], [])
        self.assertTrue(any("IPTU ZZTESTE" in e["titulo"] for e in linha.json()["eventos"]))
        self.assertFalse(any("SEI" in (e["fonte"] + e["area"] + e["titulo"]).upper() for e in linha.json()["eventos"]))
        self.assertTrue(fontes.json()["carregadas"]["vacinas"])
        self.assertTrue(fontes.json()["carregadas"]["educacao"])
        self.assertTrue(fontes.json()["carregadas"]["cemiterio"])
        self.assertEqual(self.contagens(), antes)
        with Session(self.engine) as db:
            acoes = {a.acao for a in db.scalars(select(Auditoria))}
        for acao in ("portal_consulta_cadastro", "portal_consulta_tributario", "portal_consulta_saude",
                     "portal_consulta_educacao", "portal_consulta_cemiterio", "portal_consulta_genealogia",
                     "portal_consulta_linha_do_tempo", "portal_consulta_fontes"):
            self.assertIn(acao, acoes)

    def test_arvore_so_traz_nome_e_parentesco_e_nao_grava(self):
        from app.portal_cidadao.ficha import ARESTAS, ESTADO
        ARESTAS.create(self.engine, checkfirst=True)
        ESTADO.create(self.engine, checkfirst=True)
        with self.engine.begin() as conn:
            conn.execute(ESTADO.insert().values(id=1, geracao=1, populado_em=self.datetime.now(self.UTC)))
            conn.execute(ARESTAS.insert().values(origem=self.CID, destino=self.PARENTE, sentido="ascendente",
                                                 parentesco="mãe", ativo=True, geracao=1))
            conn.execute(ARESTAS.insert().values(origem=self.CID, destino=self.OUTRO, sentido="ascendente",
                                                 parentesco="segredo", ativo=False, geracao=1))
            antes = conn.execute(_conta(ARESTAS)).scalar()
        self.entrar(self.CID)
        fontes_antes = self.contagens()
        with patch("app.genealogia.arvore.montar") as montar:
            r = self.client.get("/api/portal/me/genealogia", params={"cidadao_id": self.OUTRO})
            montar.assert_not_called()
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["parentes"], [{"nome": "Parente ZZTESTE", "parentesco": "mãe"}])
        self.assertEqual(set(r.json()["parentes"][0]), {"nome", "parentesco"})
        self.assertNotIn(self.CPF_PARENTE, r.text)
        self.assertNotIn(self.PARENTE, r.text)
        self.assertNotIn("ZZ-PARENTE", r.text)
        self.assertNotIn("segredo", r.text)
        self.assertNotIn("imovel", r.text.lower())
        with self.engine.begin() as conn:
            self.assertEqual(conn.execute(_conta(ARESTAS)).scalar(), antes)
        self.assertEqual(self.contagens(), fontes_antes)

    def test_sem_cookie_ou_com_jwt_de_servidor_nao_abre_e_a_nao_le_b(self):
        rotas_ficha = ("/api/portal/me/cadastro", "/api/portal/me/tributario", "/api/portal/me/saude",
                       "/api/portal/me/educacao", "/api/portal/me/cemiterio", "/api/portal/me/genealogia",
                       "/api/portal/me/linha-do-tempo", "/api/portal/me/fontes")
        for rota in rotas_ficha:
            self.assertEqual(self.client.get(rota).status_code, 401, rota)
        with Session(self.engine) as db:
            u = Usuario(email="admin@teste.local", nome="Teste", perfil="admin", senha_hash="somente-teste", ativo=True)
            db.add(u)
            db.commit()
            token = cria_token(u)
        self.client.cookies.set(COOKIE, token)
        for rota in rotas_ficha:
            r = self.client.get(rota, headers={"Authorization": "Bearer " + token})
            self.assertEqual(r.status_code, 401, rota)
            self.assertNotIn("ZZTESTE", r.text)
        self.assertEqual(self.client.get(f"/api/portal/me/{self.OUTRO}").status_code, 404)
        self.entrar(self.CID)
        meu = self.client.get("/api/portal/me/tributario", params={"cidadao_id": self.OUTRO}).json()
        self.assertEqual(meu["lancamentos"][0]["valor"], 150)
        self.entrar(self.OUTRO)
        dele = self.client.get("/api/portal/me/tributario").json()
        self.assertEqual(dele["lancamentos"][0]["valor"], 999)
        cad_b = self.client.get("/api/portal/me/cadastro")
        self.assertNotIn("Rua Ficticia ZZTESTE", cad_b.text)
        self.assertNotIn("Mae ZZTESTE", cad_b.text)
