# ruff: noqa: F811 - fixtures importadas do módulo da educação
"""Carga incremental (IN1, IN2, IN3, IN6, IN8, IN9, IN10a). Só dados sintéticos ZZTESTE, leitor em memória.

O leitor monta o SQL real (ConsultasGE.pagina, com `desde` parametrizado) e filtra em memória com a mesma regra:
campo_data >= desde, OR data extra (END_Endereco) >= desde, OR pessoa de aluno/responsável alterado desde então.
"""
import copy
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.connectors.gestaoescolar import consultas as cq
from tests.test_zzzzzzzzzzzzzzzzzzz_gestaoescolar import NOITE, LeitorMock, _ciclo, cfg, janela_env, limpo, origem, pid  # noqa: F401 - fixtures

DEPOIS = NOITE + timedelta(days=1)


def _ge(s):
    return s is not None and str(s) >= DESDE_STR[0]


DESDE_STR = [""]


class LeitorInc(LeitorMock):
    """Aplica o filtro incremental em memória e guarda o `desde` de cada leitura."""

    def __init__(self, dados):
        super().__init__(dados)
        self.desdes: dict[str, object] = {}

    def ler(self, fonte, ultima, limite, desde=None):
        ids = cq.mapa_tipos(self.catalogo)[0] if fonte.tipos_documento else None
        sql, params = self.q.pagina(fonte, limite, ultima, tdo_ids=ids, desde=desde)
        assert "2026-" not in sql  # data só como parâmetro
        self.desdes[fonte.nome] = desde
        dados = self.dados
        if desde is not None and fonte.campo_data:
            DESDE_STR[0] = desde.isoformat()
            alunos = {r["pes_id"] for r in dados["ACA_Aluno"] if _ge(r["alu_dataAlteracao"])}
            resp = {r["pes_id"] for r in dados["ACA_AlunoResponsavel"] if _ge(r["alr_dataAlteracao"])}
            pes = {p["pes_id"]: p for p in dados["PES_Pessoa"]}
            maes = {pes[r["pes_id"]]["pes_idFiliacaoMae"] for r in dados["ACA_Aluno"]
                    if _ge(r["alu_dataAlteracao"]) and r["pes_id"] in pes}

            def passa(r):
                if _ge(r.get(fonte.campo_data)) or any(_ge(r.get(d.split(".")[1])) for d in fonte.datas_extra):
                    return True
                return bool(fonte.escopo_pessoa) and r["pes_id"] in (alunos | resp | maes)
            dados = {**dados, fonte.nome: [r for r in dados[fonte.nome] if passa(r)]}
        saidas = [s for _e, s in fonte.colunas]
        orig, self.dados = self.dados, dados
        try:
            linhas = super().ler(fonte, ultima, limite)
        finally:
            self.dados = orig
        return [{k: v for k, v in r.items() if k in saidas or k == "tdo_classe"} for r in linhas]


@pytest.fixture
def conta_processa(monkeypatch):
    from app.services import carga_gestaoescolar as cge
    n = {"v": 0}
    real = cge.educacao.processa

    def _p(db, agora):
        n["v"] += 1
        return real(db, agora)
    monkeypatch.setattr(cge.educacao, "processa", _p)
    return n


def _inc(db, lei, c):
    from app.services import carga_gestaoescolar as cge
    for _ in range(200):
        ex = cge.incremental(db, lei, c, "zz@aion.local", agora=DEPOIS)
        if ex.status != "parcial":
            return ex
    raise AssertionError("incremental não terminou")


def _cks(db):
    from app.models.controle import CheckpointCarga
    return {c.tabela: c for c in db.scalars(select(CheckpointCarga).where(
        CheckpointCarga.sistema.in_(("GESTAOESCOLAR", "CORESSO"))))}


# ---------------------------------------------------------------- IN6: AGENDA_COMPLETA_DIAS

def test_in6_completa_dias_padrao_0_so_a_primeira_e_completa(monkeypatch):
    from app.core.config import Settings, get_settings
    from app.models.controle import Agendamento
    from app.services import agenda as ag
    monkeypatch.delenv("AGENDA_COMPLETA_DIAS", raising=False)
    get_settings.cache_clear()
    try:
        assert get_settings().agenda_completa_dias == 0
        a = Agendamento(conector="saude", horario="01:00", ativo=True, ultima_completa_em=None)
        agora = datetime(2026, 10, 29, 4, tzinfo=UTC)
        assert ag.modo_da_vez(a, agora) == "completa"             # R3: a primeira vez é completa mesmo com 0
        a.ultima_completa_em = agora - timedelta(days=400)
        assert ag.modo_da_vez(a, agora) == "incremental"
        monkeypatch.setenv("AGENDA_COMPLETA_DIAS", "0")             # 0 explícito sobe
        get_settings.cache_clear()
        assert get_settings().agenda_completa_dias == 0 and ag.modo_da_vez(a, agora) == "incremental"
        monkeypatch.setenv("AGENDA_COMPLETA_DIAS", "90")            # teto continua valendo
        get_settings.cache_clear()
        assert get_settings().agenda_completa_dias == 90
        a.ultima_completa_em = agora - timedelta(days=90)
        assert ag.modo_da_vez(a, agora) == "completa"
        a.ultima_completa_em = agora - timedelta(days=89)
        assert ag.modo_da_vez(a, agora) == "incremental"
        for ruim in ("-1", "91"):
            monkeypatch.setenv("AGENDA_COMPLETA_DIAS", ruim)
            with pytest.raises(Exception):  # noqa: B017 - ValidationError do pydantic
                Settings()
    finally:
        get_settings.cache_clear()


# ---------------------------------------------------------------- SQL

def test_sql_incremental_parametrizado_endereco_e_escopo():
    q = cq.ConsultasGE()
    d = datetime(2026, 10, 28, 4)
    end = cq.POR_NOME["PES_PessoaEndereco"]
    sql, params = q.pagina(end, 100, None, desde=d)
    assert "[pe].[pse_dataAlteracao] >= %s" in sql and "[en].[end_dataAlteracao] >= %s" in sql
    assert "[ya].[alu_dataAlteracao] >= %s" in sql and "[yr].[alr_dataAlteracao] >= %s" in sql
    assert params.count(d) == 5 and sql.count("%s") == len(params)
    doc = cq.POR_NOME["PES_PessoaDocumento"]
    sql, params = q.pagina(doc, 100, ("x", 1), tdo_ids=(1, 4), desde=d)
    assert sql.count("%s") == len(params) and params[-4:] == (d,) * 4 and "2026" not in sql
    aluno = cq.POR_NOME["ACA_Aluno"]
    sql, params = q.pagina(aluno, 100, None, desde=d)
    assert "[a].[alu_dataAlteracao] >= %s" in sql and params == (d,) and "[ya]" not in sql
    tipo = cq.POR_NOME["ACA_TipoResponsavelAluno"]  # sem campo_data: lida inteira (8 linhas)
    assert q.pagina(tipo, 100, None, desde=d) == q.pagina(tipo, 100, None)


# ---------------------------------------------------------------- fluxo

def test_in1_in2_marca_nao_zera_e_sem_mudanca_nao_refaz_prata(janela_env, limpo, conta_processa):
    from app.models.camadas import RegistroBronze
    janela_env("false")
    dados = origem()
    lei = LeitorInc(dados)
    ex = _ciclo(limpo, lei, cfg(lote=1000, max_lotes=50))
    assert ex.status == "sucesso" and ex.modo == "completa" and conta_processa["v"] == 1
    cks = _cks(limpo)
    assert cks["ACA_Aluno"].marca_dagua == datetime(2026, 10, 1, 10)       # IN1: marca gravada
    assert cks["ACA_TipoResponsavelAluno"].marca_dagua is None              # sem coluna de data
    assert not any(c.concluida or c.ultima_chave for c in cks.values())     # keyset volta ao início
    bronze = limpo.scalar(select(func.count()).select_from(RegistroBronze).where(
        RegistroBronze.sistema_origem.in_(("GESTAOESCOLAR", "CORESSO"))))
    ex = _inc(limpo, lei, cfg(lote=1000, max_lotes=50))
    assert ex.status == "sucesso" and ex.modo == "incremental", ex.mensagem
    assert ex.mensagem.startswith("Concluída · nenhuma linha nova") and conta_processa["v"] == 1
    assert (ex.novos or 0) + (ex.atualizados or 0) == 0
    assert lei.desdes["ACA_Aluno"] == datetime(2026, 10, 1, 10) - timedelta(hours=24)
    assert lei.desdes["ACA_TipoResponsavelAluno"] is None
    assert limpo.scalar(select(func.count()).select_from(RegistroBronze).where(
        RegistroBronze.sistema_origem.in_(("GESTAOESCOLAR", "CORESSO")))) == bronze
    assert _cks(limpo)["ACA_Aluno"].marca_dagua == datetime(2026, 10, 1, 10)  # IN1: não foi zerada
    assert ex.erro_classe is None  # 0 linhas na incremental não é tabela_vazia


def test_in3_alteracao_e_endereco_entram_sem_duplicar(janela_env, limpo, conta_processa):
    from app.models.camadas import EscolaAluno, RegistroBronze
    from app.models.controle import Log
    janela_env("false")
    dados = origem()
    lei = LeitorInc(dados)
    assert _ciclo(limpo, lei, cfg(lote=1000, max_lotes=50)).status == "sucesso"
    alunos = limpo.scalar(select(func.count()).select_from(EscolaAluno))
    # aluno 3 muda de nome (com data nova); endereço muda só no END_Endereco (pse_dataAlteracao igual)
    p3 = next(p for p in dados["PES_Pessoa"] if p["pes_id"] == pid(3))
    p3["pes_nome"], p3["pes_dataAlteracao"] = "ZZTESTE ALUNO TRES ALTERADO", "2026-10-28T09:00:00"
    e = dados["PES_PessoaEndereco"][0]
    e["end_logradouro"], e["end_dataAlteracao"] = "RUA ZZTESTE NOVA", "2026-10-28T09:00:00"
    antes = copy.deepcopy(dados["ACA_Aluno"])
    ex = _inc(limpo, lei, cfg(lote=1000, max_lotes=50))
    assert ex.status == "sucesso" and ex.mensagem.startswith("Incremental: Ciclo completo"), ex.mensagem
    assert conta_processa["v"] == 2 and (ex.atualizados or 0) + (ex.novos or 0) == 2
    assert limpo.scalar(select(func.count()).select_from(EscolaAluno)) == alunos   # upsert, sem duplicar
    end = limpo.scalars(select(RegistroBronze).where(RegistroBronze.tabela == "PES_PessoaEndereco")).all()
    assert len(end) == 2 and {r.id_origem for r in end} == {end[0].id_origem}      # nova versão, mesma chave
    assert all("end_dataAlteracao" not in (r.dados or {}) for r in end)
    assert _cks(limpo)["PES_Pessoa"].marca_dagua == datetime(2026, 10, 28, 9)
    logs = [m for (m,) in limpo.execute(select(Log.mensagem).where(Log.execucao_id == ex.id))]
    assert any(m.startswith("CORESSO.PES_Pessoa: incremental desde 30/09/2026 10:00; 6 lidas, 0 novas, 1 alteradas")
               for m in logs), "\n".join(logs)  # IN8
    assert not any("ZZTESTE" in m for m in logs)                                    # sem nome em log
    assert dados["ACA_Aluno"] == antes


def test_pessoa_que_entra_no_escopo_e_lida(janela_env, limpo, conta_processa):
    from app.models.camadas import RegistroBronze
    janela_env("false")
    dados = origem()
    lei = LeitorInc(dados)
    assert _ciclo(limpo, lei, cfg(lote=1000, max_lotes=50)).status == "sucesso"
    dados["PES_Pessoa"].append({"pes_id": pid(6), "pes_nome": "ZZTESTE ALUNO SEIS", "pes_nomeSocial": None,
                                "pes_dataNascimento": "2018-01-01", "pes_sexo": 1, "pes_idFiliacaoMae": None,
                                "pes_idFiliacaoPai": None, "pes_situacao": 1, "pes_dataAlteracao": "2020-01-01T00:00:00"})
    dados["ACA_Aluno"].append({"alu_id": 6, "pes_id": pid(6), "end_id": None, "alu_situacao": 1,
                               "alu_dataAlteracao": "2026-10-28T09:00:00"})
    assert _inc(limpo, lei, cfg(lote=1000, max_lotes=50)).status == "sucesso"
    ids = {r.id_origem for r in limpo.scalars(select(RegistroBronze).where(RegistroBronze.tabela == "PES_Pessoa"))}
    assert any(pid(6) in i for i in ids)  # pessoa antiga, mas aluno novo: entra


def test_incremental_parcial_continua_e_refaz_prata(janela_env, limpo, conta_processa):
    janela_env("false")
    dados = origem()
    lei = LeitorInc(dados)
    assert _ciclo(limpo, lei, cfg(lote=1000, max_lotes=50)).status == "sucesso"
    dados["ACA_Aluno"][0]["alu_situacao"], dados["ACA_Aluno"][0]["alu_dataAlteracao"] = 2, "2026-10-28T09:00:00"
    ex = _inc(limpo, lei, cfg(lote=1000, max_lotes=1))   # teto 1 lote: várias execuções parciais
    assert ex.status == "sucesso" and conta_processa["v"] == 2, ex.mensagem


def test_in9_sem_marca_usa_bronze_e_nao_rele_tudo(janela_env, limpo, conta_processa):
    """Depois da exec 320 (checkpoints zerados, sem marca): a incremental parte da maior data da bronze."""
    janela_env("false")
    dados = origem()
    lei = LeitorInc(dados)
    assert _ciclo(limpo, lei, cfg(lote=1000, max_lotes=50)).status == "sucesso"
    for c in _cks(limpo).values():
        c.marca_dagua = None
    limpo.commit()
    ex = _inc(limpo, lei, cfg(lote=1000, max_lotes=50))
    assert ex.mensagem.startswith("Concluída · nenhuma linha nova"), ex.mensagem
    assert lei.desdes["ACA_Aluno"] == datetime(2026, 10, 1, 10) - timedelta(hours=24)


def test_in6_agenda_educacao_roda_incremental(janela_env, limpo, conta_processa):
    from app.models.controle import Agendamento
    from app.services import agenda
    janela_env("false")
    dados = origem()
    lei = LeitorInc(dados)
    assert _ciclo(limpo, lei, cfg(lote=1000, max_lotes=50)).status == "sucesso"
    ag = limpo.scalar(select(Agendamento).where(Agendamento.conector == "educacao"))
    antes = ag.ultima_completa_em
    st = agenda._executar_educacao(limpo, ag, DEPOIS, leitor=lei)
    limpo.refresh(ag)
    from app.models.controle import Execucao
    ex = limpo.get(Execucao, ag.ultima_execucao_id)
    assert st in ("sucesso", "parcial") and ex.modo == "incremental", ag.ultima_mensagem
    assert ag.ultima_completa_em == antes
