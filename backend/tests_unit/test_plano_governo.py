import unittest

from app.plano_governo.dominio import PedidoInvalido, SemPermissao, aplicar, inicial, visivel

ADMIN = {"role": "admin", "email": "admin@teste.local", "name": "Gestão", "departmentIds": [], "allDepartments": True}
SEC = {"role": "secretaria", "email": "edu@teste.local", "name": "Educação", "departmentIds": ["EDUCACAO"], "allDepartments": False}
PREFEITO = {**ADMIN, "role": "prefeito"}


def base():
    s = inicial([{"id": "EDUCACAO", "name": "Educação"}, {"id": "SAUDE", "name": "Saúde"}])
    s["projects"] = []
    s["documentaryEvents"] = []
    return s


def projeto(**kwargs):
    return {"title": "Rede e laboratórios escolares", "departmentId": "EDUCACAO", "commitmentIds": [13],
            "status": "em_andamento", "progress": 20, "milestones": [], "evidence": [], **kwargs}


class TestPlano(unittest.TestCase):
    def test_carga_fiel_sem_cumprimentos_inventados(self):
        s = base()
        self.assertEqual(len(s["commitments"]), 70)
        self.assertEqual(len({c["id"] for c in s["commitments"]}), 70)
        publico = inicial(s["departments"])
        self.assertEqual(len(publico["projects"]), 49)
        self.assertTrue(all(p["progress"] is None for p in publico["projects"]))
        self.assertEqual(publico["snapshots"], [])
        self.assertTrue(all(c["validation"] == "a_validar" for c in s["commitments"]))
        self.assertEqual(sum(c["researchStatus"] == "Entrega comprovada" for c in s["commitments"]), 13)
        self.assertEqual(s["commitments"][0]["departmentId"], "")

    def test_multiplos_projetos_e_multiplos_compromissos(self):
        s = aplicar(base(), "project.save", projeto(commitmentIds=[13, 22, 13]), ADMIN)
        s = aplicar(s, "project.save", projeto(title="Conectividade"), ADMIN)
        self.assertEqual(len(s["projects"]), 2)
        self.assertEqual(s["projects"][0]["commitmentIds"], [13, 22])
        self.assertEqual(len(s["history"]), 2)

    def test_referencias_obrigatorias(self):
        for args in ({"departmentId": ""}, {"commitmentIds": []}, {"commitmentIds": [71]}, {"ownerId": "inexistente"}):
            with self.subTest(args=args), self.assertRaises(PedidoInvalido):
                aplicar(base(), "project.save", projeto(**args), ADMIN)

    def test_responsavel_individual(self):
        s = aplicar(base(), "person.save", {"name": "Ponto focal", "email": SEC["email"]}, ADMIN)
        s = aplicar(s, "project.save", projeto(departmentId="", ownerId=s["people"][0]["id"]), ADMIN)
        self.assertEqual(len(visivel(s, {**SEC, "departmentIds": []})["projects"]), 1)
        self.assertEqual(s["projects"][0]["departmentId"], "")

    def test_escopo_vigente_e_atribuicao_nao_permite_outra_secretaria(self):
        s = aplicar(base(), "project.save", projeto(), ADMIN)
        self.assertEqual(len(visivel(s, SEC)["projects"]), 1)
        saude = {**SEC, "departmentIds": ["SAUDE"]}
        self.assertEqual(visivel(s, saude)["projects"], [])
        with self.assertRaises(SemPermissao):
            aplicar(s, "project.save", s["projects"][0], saude)
        with self.assertRaises(SemPermissao):
            aplicar(s, "project.save", {**s["projects"][0], "departmentId": "SAUDE"}, SEC)
        with self.assertRaises(SemPermissao):
            aplicar(s, "project.save", projeto(commitmentIds=[46]), SEC)

    def test_pesos_dos_marcos(self):
        s = aplicar(base(), "project.save", projeto(progress=99, milestones=[
            {"title": "Contrato", "weight": 1, "done": True}, {"title": "Entrega", "weight": 3, "done": False}]), ADMIN)
        self.assertEqual(s["projects"][0]["progress"], 25)

    def test_evidencia_e_progresso_para_concluir(self):
        with self.assertRaises(PedidoInvalido):
            aplicar(base(), "project.save", projeto(status="concluido", progress=100), ADMIN)
        for url in ("javascript:alert(1)", "file:///etc/passwd", "https:"):
            with self.subTest(url=url), self.assertRaises(PedidoInvalido):
                aplicar(base(), "project.save", projeto(evidence=[{"url": url}]), ADMIN)

    def test_validacao_separada_e_reabertura_exige_revisao(self):
        s = aplicar(base(), "project.save", projeto(status="concluido", progress=100,
                    evidence=[{"url": "https://franca.sp.gov.br/entrega"}]), ADMIN)
        self.assertEqual(s["commitments"][12]["validation"], "a_validar")
        s = aplicar(s, "commitment.save", {"id": 13, "departmentId": "EDUCACAO", "validation": "cumprida",
                    "validationNote": "Entrega aprovada", "validationEvidence": "https://franca.sp.gov.br/entrega"}, PREFEITO)
        self.assertEqual(s["commitments"][12]["validation"], "cumprida")
        s = aplicar(s, "project.save", {**s["projects"][0], "status": "em_andamento", "progress": 80}, ADMIN)
        self.assertEqual(s["commitments"][12]["validation"], "a_validar")

    def test_gabinete_valida_mas_nao_edita_projetos_e_leitura_nao_edita(self):
        for u in (PREFEITO, {**SEC, "role": "leitura"}):
            with self.subTest(u=u), self.assertRaises(SemPermissao):
                aplicar(base(), "project.save", projeto(), u)
        with self.assertRaises(SemPermissao):
            aplicar(base(), "person.save", {"name": "Pessoa"}, SEC)

    def test_datas_impedimento_e_valores(self):
        for args in ({"due": "2026-02-30"}, {"start": "2027-01-01", "due": "2026-12-01"},
                     {"status": "bloqueado"}, {"progress": 120}, {"budget": -1}, {"spent": "NaN"}):
            with self.subTest(args=args), self.assertRaises(PedidoInvalido):
                aplicar(base(), "project.save", projeto(**args), ADMIN)

    def test_historico_e_arquivamento_reversivel(self):
        s = aplicar(base(), "project.save", projeto(updateNote="Primeiro levantamento."), ADMIN)
        self.assertEqual(len(s["updates"]), 1)
        idp = s["projects"][0]["id"]
        s = aplicar(s, "project.archive", {"id": idp}, ADMIN)
        self.assertTrue(s["projects"][0]["archived"])
        s = aplicar(s, "project.archive", {"id": idp}, ADMIN)
        self.assertFalse(s["projects"][0]["archived"])
        self.assertEqual(len(s["history"]), 3)

    def test_visibilidade_do_historico_nao_vaza_outras_secretarias(self):
        s = aplicar(base(), "project.save", projeto(), ADMIN)
        s = aplicar(s, "project.save", projeto(title="Projeto da Saúde", departmentId="SAUDE", commitmentIds=[46]), ADMIN)
        r = visivel(s, SEC)
        self.assertFalse(any("Saúde" in h["message"] for h in r["history"]))
        self.assertFalse(any(c["id"] == 46 for c in r["commitments"]))

    def test_importacao_idempotente_preserva_gestao(self):
        from app.plano_governo.dominio import atualizar_base
        s = base()
        s.pop("baseline", None)
        s.pop("documentaryBaseline", None)
        s = aplicar(s, "project.save", projeto(), ADMIN)
        atual = atualizar_base(s, s["departments"])
        self.assertEqual(atual["projects"][0]["progress"], 20)
        self.assertEqual(atualizar_base(atual, s["departments"]), atual)

    def test_medicoes_sao_imutaveis_e_desconhecido_nao_vira_zero(self):
        s = aplicar(base(), "project.save", projeto(progress=None), ADMIN)
        self.assertIsNone(s["snapshots"][0]["progress"])
        s = aplicar(s, "project.save", {**s["projects"][0], "progress": 35}, ADMIN)
        s = aplicar(s, "project.save", {**s["projects"][0], "progress": 70}, ADMIN)
        self.assertEqual([x["progress"] for x in s["snapshots"]], [None, 35, 70])

    def test_conclusao_nao_aceita_marco_incompleto_arredondado(self):
        with self.assertRaises(PedidoInvalido):
            aplicar(base(), "project.save", projeto(status="concluido", milestones=[
                {"title": "Obra", "weight": 1000, "done": True},
                {"title": "Aceite", "weight": .01, "done": False}],
                evidence=[{"url": "https://franca.sp.gov.br/entrega"}]), ADMIN)

    def test_tarefa_responsavel_prazo_e_avanco(self):
        s = aplicar(base(), "person.save", {"name": "Responsável da tarefa", "email": "ponto@teste.local"}, ADMIN)
        dono = s["people"][0]["id"]
        dados = projeto(start="2026-11-01", due="2026-12-31", milestones=[
            {"title": "Aceite", "due": "2026-12-20", "ownerId": dono, "done": False, "weight": 1}])
        s = aplicar(s, "project.save", dados, ADMIN)
        self.assertEqual(s["projects"][0]["milestones"][0]["ownerId"], dono)
        self.assertEqual(s["projects"][0]["progress"], 0)
        with self.assertRaises(PedidoInvalido):
            aplicar(s, "project.save", {**dados, "milestones": [{"title": "Fora do prazo", "due": "2027-01-01"}]}, ADMIN)
