"""Conector de educação (FRA_GestaoEscolar + FRA_CoreSSO): GE3, GE4, GE6–GE11. Só dados sintéticos ZZTESTE.

Os CPFs são gerados aqui a partir de bases 999000xxx (prefixo sintético) só para ter DV válido; não são de ninguém.
Nenhuma origem real: o leitor é um mock em memória que também monta o SQL real (ConsultasGE) e o passa pelo guard.
"""
import re
import uuid
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import delete, func, select

from app.connectors.gestaoescolar import consultas as cq
from app.connectors.sqlserver.politicas import TabelaRecusada
from app.core.sql_guard import garante_somente_leitura
from app.identity import correlacao as corr
from app.identity.cpf import digitos_verificadores

NOITE = datetime(2026, 10, 29, 5, 0, tzinfo=UTC)   # 02:00 BRT, dentro de 00:00-06:00
DIA = datetime(2026, 10, 29, 15, 0, tzinfo=UTC)    # 12:00 BRT


def cpf_sintetico(n: int) -> str:
    base = f"999000{n:03d}"
    return base + digitos_verificadores(base)


def pid(n: int) -> str:
    return str(uuid.UUID(int=0x2222_0000_0000_0000_0000_0000_0000_0000 + n))


# ---- origem sintética ----
def origem() -> dict[str, list[dict]]:
    """Alunos: 1 com CPF (vinculado), 2 sem CPF que casa nome+nasc+mãe (revisão), 3 sem nada (quarentena),
    4 com aluno situação 3 (inativo), 5 com pes_id ausente do CoreSSO (quarentena)."""
    P = cq
    pes = [
        {"pes_id": pid(1), "pes_nome": "ZZTESTE ALUNO UM", "pes_nomeSocial": None, "pes_dataNascimento": "2015-03-01",
         "pes_sexo": 1, "pes_idFiliacaoMae": pid(11), "pes_idFiliacaoPai": None, "pes_situacao": 1,
         "pes_dataAlteracao": "2026-10-01T10:00:00"},
        {"pes_id": pid(2), "pes_nome": "ZZTESTE ALUNO DOIS", "pes_nomeSocial": None, "pes_dataNascimento": "2016-04-02",
         "pes_sexo": 2, "pes_idFiliacaoMae": None, "pes_idFiliacaoPai": None, "pes_situacao": 1,
         "pes_dataAlteracao": "2026-10-01T10:00:00"},
        {"pes_id": pid(3), "pes_nome": "ZZTESTE ALUNO TRES", "pes_nomeSocial": None, "pes_dataNascimento": "2017-05-03",
         "pes_sexo": 1, "pes_idFiliacaoMae": None, "pes_idFiliacaoPai": None, "pes_situacao": 1,
         "pes_dataAlteracao": "2026-10-01T10:00:00"},
        {"pes_id": pid(4), "pes_nome": "ZZTESTE ALUNO QUATRO", "pes_nomeSocial": None, "pes_dataNascimento": "2014-06-04",
         "pes_sexo": 1, "pes_idFiliacaoMae": None, "pes_idFiliacaoPai": None, "pes_situacao": 1,
         "pes_dataAlteracao": "2026-10-01T10:00:00"},
        {"pes_id": pid(11), "pes_nome": "ZZTESTE MAE UM", "pes_nomeSocial": None, "pes_dataNascimento": "1985-01-01",
         "pes_sexo": 2, "pes_idFiliacaoMae": None, "pes_idFiliacaoPai": None, "pes_situacao": 1,
         "pes_dataAlteracao": "2026-10-01T10:00:00"},
        {"pes_id": pid(12), "pes_nome": "ZZTESTE MAE DOIS", "pes_nomeSocial": None, "pes_dataNascimento": "1986-01-01",
         "pes_sexo": 2, "pes_idFiliacaoMae": None, "pes_idFiliacaoPai": None, "pes_situacao": 1,
         "pes_dataAlteracao": "2026-10-01T10:00:00"},
    ]
    docs = [
        {"pes_id": pid(1), "tdo_id": 1, "psd_numero": cpf_sintetico(1), "psd_situacao": 1,
         "psd_dataAlteracao": "2026-10-01T10:00:00", "tdo_classificacao": 1},
        {"pes_id": pid(2), "tdo_id": 4, "psd_numero": "99900000002", "psd_situacao": 1,
         "psd_dataAlteracao": "2026-10-01T10:00:00", "tdo_classificacao": 4},
        {"pes_id": pid(11), "tdo_id": 1, "psd_numero": cpf_sintetico(11), "psd_situacao": 1,
         "psd_dataAlteracao": "2026-10-01T10:00:00", "tdo_classificacao": 1},
    ]
    alunos = [{"alu_id": i, "pes_id": pid(i), "end_id": None, "alu_situacao": 3 if i == 4 else 1,
               "alu_dataAlteracao": "2026-10-01T10:00:00"} for i in (1, 2, 3, 4, 5)]
    curr = [{"alu_id": 1, "alc_id": 1, "alc_matricula": "ZZ0001", "esc_id": 900, "uni_id": 1, "cur_id": 1, "crr_id": 1,
             "crp_id": 1, "alc_situacao": 1, "alc_dataAlteracao": "2026-10-01T10:00:00"},
            {"alu_id": 1, "alc_id": 2, "alc_matricula": "ZZ0002", "esc_id": 900, "uni_id": 1, "cur_id": 1, "crr_id": 1,
             "crp_id": 2, "alc_situacao": 1, "alc_dataAlteracao": "2026-10-01T10:00:00"}]
    mats = [{"alu_id": 1, "mtu_id": 1, "alc_id": 1, "tur_id": 70, "cur_id": 1, "crr_id": 1, "crp_id": 1, "mtu_situacao": 3,
             "mtu_dataAlteracao": "2025-02-01T10:00:00"},
            {"alu_id": 1, "mtu_id": 2, "alc_id": 2, "tur_id": 71, "cur_id": 1, "crr_id": 1, "crp_id": 2, "mtu_situacao": 1,
             "mtu_dataAlteracao": "2026-02-01T10:00:00"},
            {"alu_id": 1, "mtu_id": 3, "alc_id": 2, "tur_id": 71, "cur_id": 1, "crr_id": 1, "crp_id": 2, "mtu_situacao": 5,
             "mtu_dataAlteracao": "2026-01-01T10:00:00"}]
    return {
        "ACA_Aluno": alunos, "ACA_AlunoCurriculo": curr, "MTR_MatriculaTurma": mats,
        "TUR_TurmaCurriculo": [{"tur_id": t, "cur_id": 1, "crr_id": 1, "crp_id": t - 69, "tcr_situacao": 1,
                                "tcr_dataAlteracao": "2026-01-01T00:00:00"} for t in (70, 71)],
        "TUR_Turma": [{"tur_id": t, "esc_id": 900, "uni_id": 1, "tur_codigo": f"ZZ-{t}", "tur_situacao": 1,
                       "tur_dataAlteracao": "2026-01-01T00:00:00"} for t in (70, 71)],
        "ESC_UnidadeEscola": [{"esc_id": 900, "uni_id": 1, "uni_descricao": "ZZTESTE UNIDADE SEDE", "uni_situacao": 1,
                               "uni_dataAlteracao": "2026-01-01T00:00:00"}],
        "ESC_Escola": [{"esc_id": 900, "esc_nome": "ZZTESTE ESCOLA MUNICIPAL", "esc_codigo": "ZZ900",
                        "esc_codigoInep": "99999999", "esc_situacao": 1, "esc_dataAlteracao": "2026-01-01T00:00:00"}],
        "ACA_AlunoResponsavel": [
            {"alu_id": 1, "alr_id": 1, "tra_id": 1, "pes_id": pid(11), "alr_principal": True,
             "alr_constaCertidaoNascimento": True, "alr_apenasFiliacao": False, "alr_moraComAluno": True,
             "alr_situacao": 1, "alr_dataAlteracao": "2026-01-01T00:00:00"},
            {"alu_id": 2, "alr_id": 1, "tra_id": 1, "pes_id": pid(12), "alr_principal": True,
             "alr_constaCertidaoNascimento": True, "alr_apenasFiliacao": False, "alr_moraComAluno": True,
             "alr_situacao": 1, "alr_dataAlteracao": "2026-01-01T00:00:00"}],
        "ACA_TipoResponsavelAluno": [{"tra_id": 1, "tra_nome": "Filiação 1", "tra_tipoResponsavelPadrao": 1, "tra_situacao": 1},
                                     {"tra_id": 2, "tra_nome": "Filiação 2", "tra_tipoResponsavelPadrao": 2, "tra_situacao": 1}],
        "PES_Pessoa": pes, "PES_PessoaDocumento": docs,
        "PES_CertidaoCivil": [{"pes_id": pid(3), "ctc_id": 1, "ctc_tipo": 1, "ctc_modeloNovo": True,
                               "ctc_matricula": "9" * 32, "ctc_situacao": 1, "ctc_dataAlteracao": "2026-01-01T00:00:00"}],
        "PES_PessoaEndereco": [{"pes_id": pid(1), "pse_id": 1, "pse_numero": "100", "pse_complemento": None,
                                "pse_enderecoPrincipal": 1, "pse_situacao": 1, "pse_dataAlteracao": "2026-01-01T00:00:00",
                                "end_cep": "99999-000", "end_logradouro": "RUA ZZTESTE", "end_bairro": "BAIRRO ZZTESTE"}],
    }
    del P


class LeitorMock:
    """Mesma interface do LeitorGE: monta o SQL real (e passa pelo guard), devolve a página por keyset em memória."""

    def __init__(self, dados):
        self.dados = dados
        self.sqls: list[str] = []
        self.q = cq.ConsultasGE()

    def ler(self, fonte, ultima, limite):
        sql, _params = self.q.pagina(fonte, limite, ultima)
        self.sqls.append(garante_somente_leitura(sql))
        linhas = sorted(self.dados.get(fonte.nome, []), key=lambda r: tuple(r[k] for k in fonte.chave))
        if ultima is not None:
            linhas = [r for r in linhas if tuple(r[k] for k in fonte.chave) > tuple(ultima)]
        return [dict(r) for r in linhas[:limite]]


def cfg(lote=1000, max_lotes=1):
    return cq.ConfigGE("zz-host-teste", 1433, "zz_leitor_teste", "zz-senha-teste", "FRA_GestaoEscolar", "FRA_CoreSSO",
                       lote, max_lotes, 50)


@pytest.fixture
def limpo(db):
    from app.models.camadas import CorrelacaoPendente, EscolaAluno, EscolaMatricula, EscolaResponsavel, Municipe, RegistroBronze
    from app.models.controle import CheckpointCarga
    for m in (EscolaAluno, EscolaMatricula, EscolaResponsavel):
        db.execute(delete(m))
    db.execute(delete(RegistroBronze).where(RegistroBronze.sistema_origem.in_(("GESTAOESCOLAR", "CORESSO"))))
    db.execute(delete(CheckpointCarga).where(CheckpointCarga.sistema.in_(("GESTAOESCOLAR", "CORESSO"))))
    db.execute(delete(CorrelacaoPendente).where(CorrelacaoPendente.sistema == "GESTAOESCOLAR"))
    db.execute(delete(Municipe).where(Municipe.nome.like("ZZTESTE%")))
    db.commit()
    return db


def _municipe_aluno2(db):
    """Cidadão já conhecido com nome+nascimento+mãe do aluno 2 (sem CPF na escola) -> revisão."""
    from app.models.camadas import Municipe
    from app.models.controle import Execucao
    ex = Execucao(tipo="zz_teste", sistema="ISS", status="sucesso")
    db.add(ex)
    db.flush()
    db.add(Municipe(documento=cpf_sintetico(2), tipo="cidadao", tipo_pessoa="F", nome="ZZTESTE ALUNO DOIS",
                    nome_mae="ZZTESTE MAE DOIS", data_nascimento=date(2016, 4, 2),
                    cidadao_id=corr.cidadao_id_por_cpf(cpf_sintetico(2)), hash="zz", execucao_id=ex.id))
    db.commit()


def _ciclo(db, leitor, c):
    from app.services import carga_gestaoescolar as cge
    a = cge.amostra(db, leitor, c, "zz@aion.local")
    assert a.status == "sucesso", a.mensagem
    for _ in range(200):
        ex = cge.completa(db, leitor, c, "zz@aion.local", agora=NOITE)
        if ex.status != "parcial":
            return ex
    raise AssertionError("ciclo não terminou")


# ---------------------------------------------------------------- GE10 / GE2
def test_ge10_nenhuma_tabela_ou_coluna_excluida_e_lida():
    cq.valida_fontes()
    q = cq.ConsultasGE()
    proibidas_tab = ["ACA_AlunoFichaMedica", "ACA_AlunoIMC", "ACA_AlunoComprovacaoDeficiencia", "PES_PessoaDeficiencia",
                     "ACA_Religiao", "ACA_PovoIndigena", "ACA_AlunoFormularioSocioeconomico", "ACA_AlunoProgramaSocial",
                     "SYS_Usuario", "SYS_UsuarioSenhaHistorico", "VW_SYS_Usuario", "InscricaoListaUsuariosErro",
                     "BNF_BolsaFamilia_X", "ACA_AlunoAtendimentoEspecial"]
    for t in proibidas_tab:
        assert cq.tabela_excluida(t), t
        with pytest.raises(TabelaRecusada):
            q._tab("escolar", t)
    for c in ("pes_racaCor", "pes_foto", "arq_idFoto", "rlg_id", "pin_id", "alu_rendaFamiliar", "alu_tratamentoSaude",
              "alr_programaSocial", "alu_tipoMobilidadeReduzida", "alr_rendaFamiliarMensal", "psd_infoComplementares"):
        assert cq.coluna_excluida(c), c
    texto = " ".join(q.pagina(f, 10, None)[0] for f in cq.FONTES)
    for t in proibidas_tab:
        assert t.lower() not in texto.lower()
    for c in cq.COLUNAS_EXCLUIDAS:
        assert not re.search(rf"\[{re.escape(c)}\]", texto, re.IGNORECASE), c
    idents = set(re.findall(r"\[([A-Za-z0-9_]+)\]", texto))
    assert {i for i in idents if cq.coluna_excluida(i) or cq.tabela_excluida(i)} == set()
    for padrao in ("racacor", "foto", "religi", "indigena", "deficien", "socioeconomico", "fichamedica", "sys_usuario"):
        assert not [i for i in idents if padrao in i.lower()], padrao


def test_ge10_fonte_com_coluna_ou_tabela_excluida_e_recusada():
    ruim = cq.Fonte("ACA_Aluno", "GESTAOESCOLAR", "escolar", "ACA_Aluno", "a", (("a.alu_id", "alu_id"), ("a.rlg_id", "rlg_id")),
                    ("alu_id",))
    with pytest.raises(TabelaRecusada):
        cq.valida_fontes((ruim,))
    ruim2 = cq.Fonte("X", "GESTAOESCOLAR", "escolar", "ACA_AlunoFichaMedica", "a", (("a.alu_id", "alu_id"),), ("alu_id",))
    with pytest.raises(TabelaRecusada):
        cq.valida_fontes((ruim2,))
    with pytest.raises(TabelaRecusada):  # GE2: fora da lista, recusa antes da rede
        cq.ConsultasGE().pagina(ruim2, 10)


# ---------------------------------------------------------------- GE11
def test_ge11_select_nolock_top_keyset_e_guard():
    q = cq.ConsultasGE()
    for f in cq.FONTES:
        sql, params = q.pagina(f, 7, None)
        assert sql.startswith("SELECT TOP (7) ") and "*" not in sql
        refs = len(re.findall(r"\]\.\[dbo\]\.\[", sql))
        assert sql.count("WITH (NOLOCK)") == refs >= 1, f.nome
        assert garante_somente_leitura(sql) == sql
        assert params == ()
        sql2, p2 = q.pagina(f, 7, tuple(1 for _ in f.chave))
        assert "%s" in sql2 and len(p2) == len(f.chave) * (len(f.chave) + 1) // 2
    # CoreSSO só no escopo da escola, por junção direta entre os bancos (pes_id)
    sql = q.pagina("PES_Pessoa", 5)[0]
    assert "[FRA_GestaoEscolar].[dbo].[ACA_Aluno]" in sql and "[pes_idFiliacaoMae]" in sql
    assert "IN (1, 3, 4)" in q.pagina("PES_PessoaDocumento", 5)[0]


def test_ge11_credencial_so_do_ambiente_e_nunca_sistema(monkeypatch):
    from app.core.config import get_settings
    for nome in ("sistema", "SISTEMA", "DOMINIO\\sistema", " Sistema ", "sistema@dominio", "SISTEMA@Prefeitura.local",
                 " sistema@x ", "DOMINIO\\sistema@dominio"):
        c = cfg()
        c.user = nome
        with pytest.raises(PermissionError):
            c.valida()
    vazio = cfg()
    vazio.password = None
    with pytest.raises(PermissionError):
        vazio.valida()
    c = cfg()
    c.valida()
    assert "zz_leitor_teste" not in repr(c) and "zz-senha-teste" not in repr(c) and "zz-host" not in str(c)
    monkeypatch.setenv("SIGPMF_SRC_GESTAOESCOLAR_USER", "zz_env_user")
    monkeypatch.setenv("SIGPMF_SRC_GESTAOESCOLAR_PASSWORD", "zz-env-pass")
    monkeypatch.setenv("SIGPMF_SRC_GESTAOESCOLAR_HOST", "zz-env-host")
    get_settings.cache_clear()
    try:
        c = cq.ConfigGE.do_ambiente(get_settings())
        assert (c.user, c.password, c.max_lotes, c.amostra) == ("zz_env_user", "zz-env-pass", 1, 50)
    finally:
        get_settings.cache_clear()


def test_ge11_sem_amostra_e_fora_da_janela_recusa(limpo):
    from app.models.controle import Execucao
    from app.services import carga_gestaoescolar as cge
    db = limpo
    db.add(Execucao(tipo=cge.TIPO_AMOSTRA, sistema="GESTAOESCOLAR", status="falha"))
    db.commit()
    with pytest.raises(cge.CargaRecusada, match="amostra"):
        cge.completa(db, LeitorMock(origem()), cfg(), agora=NOITE)
    assert cge.amostra(db, LeitorMock(origem()), cfg()).status == "sucesso"
    with pytest.raises(cge.CargaRecusada, match="janela"):
        cge.completa(db, LeitorMock(origem()), cfg(), agora=DIA)


def test_ge3_amostra_le_no_maximo_50_por_tabela(limpo):
    from app.services import carga_gestaoescolar as cge
    dados = origem()
    dados["ACA_Aluno"] = [dict(dados["ACA_Aluno"][0], alu_id=1000 + i) for i in range(80)]
    lei = LeitorMock(dados)
    ex = cge.amostra(limpo, lei, cfg())
    assert ex.status == "sucesso"
    assert all(s.startswith("SELECT TOP (50) ") for s in lei.sqls) and len(lei.sqls) == len(cq.FONTES)


# ---------------------------------------------------------------- GE8: teto de lotes, checkpoint, hash por tabela
def test_ge8_teto_de_lotes_checkpoint_sem_lacuna_e_hash_por_tabela(limpo):
    from app.models.camadas import RegistroBronze
    from app.models.controle import CheckpointCarga
    from app.services import carga_gestaoescolar as cge
    db = limpo
    dados = origem()
    lei = LeitorMock(dados)
    assert cge.amostra(db, lei, cfg(lote=2)).status == "sucesso"
    ex = cge.completa(db, lei, cfg(lote=2, max_lotes=1), agora=NOITE)
    assert ex.status == "parcial" and ex.lotes == 1  # teto padrão 1 respeitado
    ck = db.scalar(select(CheckpointCarga).where(CheckpointCarga.sistema == "GESTAOESCOLAR",
                                                 CheckpointCarga.tabela == "ACA_Aluno"))
    assert ck.ultima_chave == [2] and not ck.concluida
    final = _ciclo(db, lei, cfg(lote=2, max_lotes=1))
    assert final.status == "sucesso", final.mensagem
    for nome, linhas in dados.items():
        f = cq.POR_NOME[nome]
        ids = set(db.scalars(select(RegistroBronze.id_origem).where(RegistroBronze.sistema_origem == f.sistema,
                                                                    RegistroBronze.tabela == nome)))
        assert len(ids) == len(linhas), nome  # nenhuma linha pulada nem duplicada
    # ciclo concluído zera os checkpoints
    assert all(not c.concluida and c.ultima_chave is None for c in db.scalars(
        select(CheckpointCarga).where(CheckpointCarga.sistema.in_(("GESTAOESCOLAR", "CORESSO")))))
    # documento muda sem mudar a pessoa: só a tabela de documentos ganha versão nova (hash próprio)
    antes = dict(db.execute(select(RegistroBronze.tabela, func.count()).where(
        RegistroBronze.sistema_origem.in_(("GESTAOESCOLAR", "CORESSO"))).group_by(RegistroBronze.tabela)).all())
    dados["PES_PessoaDocumento"][1]["psd_numero"] = "99900000099"
    assert _ciclo(db, lei, cfg(lote=1000, max_lotes=50)).status == "sucesso"
    depois = dict(db.execute(select(RegistroBronze.tabela, func.count()).where(
        RegistroBronze.sistema_origem.in_(("GESTAOESCOLAR", "CORESSO"))).group_by(RegistroBronze.tabela)).all())
    mudou = {t for t in depois if depois[t] != antes.get(t)}
    assert mudou == {"PES_PessoaDocumento"} and depois["PES_PessoaDocumento"] == antes["PES_PessoaDocumento"] + 1


# ---------------------------------------------------------------- GE6 / GE7 / GE9
def test_ge6_ge9_casamento_quarentena_e_exclusao_logica(limpo):
    from app.models.camadas import CorrelacaoPendente, EscolaAluno, EscolaMatricula, EscolaResponsavel, RegistroBronze
    from app.models.controle import Log
    db = limpo
    _municipe_aluno2(db)
    ex = _ciclo(db, LeitorMock(origem()), cfg(max_lotes=100))
    assert ex.status == "sucesso"
    al = {a.alu_id: a for a in db.scalars(select(EscolaAluno))}
    # GE6a: CPF válido -> vínculo automático, peso 1.0; mãe por pes_idFiliacaoMae; endereço do CoreSSO
    assert al[1].status_vinculo == "vinculado" and al[1].confianca == 1.0
    assert al[1].cidadao_id == corr.cidadao_id_por_cpf(cpf_sintetico(1))
    assert al[1].nome_mae == "ZZTESTE MAE UM" and al[1].cep == "99999000" and al[1].logradouro == "RUA ZZTESTE"
    # GE6b: sem CPF, nome+nasc+mãe (mãe pelo responsável tra_id=1) -> revisão, nunca vínculo; NIS (classificação 4) guardado
    assert al[2].status_vinculo == "revisao" and al[2].cidadao_id is None and al[2].motivo == "revisao_nome_nascimento_mae"
    assert al[2].nome_mae == "ZZTESTE MAE DOIS" and al[2].nis == "99900000002"
    pend = db.scalar(select(CorrelacaoPendente).where(CorrelacaoPendente.sistema == "GESTAOESCOLAR",
                                                      CorrelacaoPendente.chave_origem == "2"))
    assert pend and pend.motivo == "revisao_nome_nascimento_mae" and pend.nome is None
    # GE6c/GE7: sem casamento -> quarentena com motivo (no aluno e na bronze); certidão guardada
    assert al[3].status_vinculo == "quarentena" and al[3].motivo == "sem_cpf_sem_casamento" and al[3].certidao == "9" * 32
    assert al[5].status_vinculo == "quarentena" and al[5].motivo == "pessoa_ausente_no_coresso"
    q = dict(db.execute(select(RegistroBronze.id_origem, RegistroBronze.quarentena).where(
        RegistroBronze.sistema_origem == "GESTAOESCOLAR", RegistroBronze.tabela == "ACA_Aluno",
        RegistroBronze.quarentena.is_not(None))).all())
    assert q == {"3": "sem_cpf_sem_casamento", "5": "pessoa_ausente_no_coresso"}
    # situação 3 no aluno: exclusão lógica (inativo), linha mantida
    assert al[4].excluido and al[4].status_vinculo == "excluido"
    # GE9: todas as situações no histórico; só a situação 1 é atual; situação 3 = excluída (sem DELETE)
    mats = {m.mtu_id: m for m in db.scalars(select(EscolaMatricula).where(EscolaMatricula.alu_id == 1))}
    assert set(mats) == {1, 2, 3}
    assert mats[1].excluida and not mats[1].atual and mats[1].situacao == 3
    assert mats[2].atual and mats[2].escola == "ZZTESTE ESCOLA MUNICIPAL" and mats[2].turma == "ZZ-71"
    assert not mats[3].atual and not mats[3].excluida and mats[3].situacao == 5
    assert db.scalar(select(EscolaResponsavel.tipo).where(EscolaResponsavel.alu_id == 1)) == "Filiação 1"
    # logs só com contagens: nenhum nome, CPF ou pes_id
    msgs = " ".join(db.scalars(select(Log.mensagem).where(Log.execucao_id == ex.id))) + (ex.mensagem or "")
    assert "ZZTESTE" not in msgs and cpf_sintetico(1) not in msgs and pid(1) not in msgs
    # reprocessar: matrícula passa a situação 3 -> só marca, não apaga
    dados = origem()
    dados["MTR_MatriculaTurma"][1]["mtu_situacao"] = 3
    _ciclo(db, LeitorMock(dados), cfg(max_lotes=100))
    db.expire_all()
    mats = {m.mtu_id: m for m in db.scalars(select(EscolaMatricula).where(EscolaMatricula.alu_id == 1))}
    assert set(mats) == {1, 2, 3} and mats[2].excluida and not any(m.atual for m in mats.values())


# ---------------------------------------------------------------- GE4: máscara de menores e revelar
def test_ge4_menor_mascarado_e_revelar_so_admin_lgpd_com_motivo(limpo, client, tokens):
    from app.models.camadas import Cidadao
    from app.models.controle import Auditoria
    db = limpo
    _ciclo(db, LeitorMock(origem()), cfg(max_lotes=100))
    cid = corr.cidadao_id_por_cpf(cpf_sintetico(1))
    if db.get(Cidadao, cid) is None:
        db.add(Cidadao(cidadao_id=cid, nome="ZZTESTE ALUNO UM", data_nascimento=date(2015, 3, 1)))
        db.commit()
    for perfil in ("operador", "admin", "admin_lgpd"):
        r = client.get(f"/api/gold/cidadaos/{cid}/educacao", headers=tokens[perfil])
        assert r.status_code == 200, r.text
        a = r.json()["alunos"][0]
        assert a["menor"] and a["mascarado"] and a["nome"] != "ZZTESTE ALUNO UM" and a["data_nascimento"] is None
        assert a["cpf"].startswith("***") and a["endereco"]["logradouro"] == "***"
        assert a["responsaveis"][0]["nome"] != "ZZTESTE MAE UM"
        assert a["matricula_atual"]["turma"] == "ZZ-71" and a["matricula_atual"]["unidade"] == "ZZTESTE UNIDADE SEDE"
        # situação 3 (mtu 1) some; situação 5 (mtu 3) fica no histórico como inativa
        assert [(h["situacao"], h["inativa"]) for h in a["historico"]] == [(5, True), (1, False)]
    r = client.post(f"/api/gold/cidadaos/{cid}/educacao/revelar", json={"motivo": "atendimento zzteste"},
                    headers=tokens["operador"])
    assert r.status_code == 403
    r = client.post(f"/api/gold/cidadaos/{cid}/educacao/revelar", json={"motivo": "x"}, headers=tokens["admin_lgpd"])
    assert r.status_code == 400
    r = client.post(f"/api/gold/cidadaos/{cid}/educacao/revelar", json={"motivo": "atendimento zzteste ok"},
                    headers=tokens["admin_lgpd"])
    assert r.status_code == 200
    a = r.json()["alunos"][0]
    assert a["nome"] == "ZZTESTE ALUNO UM" and a["cpf"] == cpf_sintetico(1) and a["responsaveis"][0]["nome"] == "ZZTESTE MAE UM"
    aud = db.scalar(select(Auditoria).where(Auditoria.acao == "revelar_educacao").order_by(Auditoria.id.desc()))
    assert aud and aud.detalhe["motivo"] == "atendimento zzteste ok" and cpf_sintetico(1) not in str(aud.detalhe)


def test_ge11_rota_recusa_sem_credencial(client, tokens):
    r = client.post("/api/educacao/conector/amostra", headers=tokens["admin"])
    assert r.status_code == 409 and "não configurada" in r.json()["detail"]
    assert client.post("/api/educacao/conector/amostra", headers=tokens["operador"]).status_code == 403


# ---------------------------------------------------------------- sequência do #28
def test_b1_usuario_base_remove_dominio_e_sufixo():
    assert cq.usuario_base(" DOMINIO\\Sistema@dominio ") == "sistema"
    assert cq.usuario_base("zz_leitor_teste@dominio") == "zz_leitor_teste"
    c = cfg()
    c.user = "sistema2@dominio"
    c.valida()  # só o login `sistema` exato é recusado


def test_b2_revelar_sem_motivo_e_auditado(limpo, client, tokens):
    from app.models.camadas import Cidadao
    from app.models.controle import Auditoria
    db = limpo
    cid = corr.cidadao_id_por_cpf(cpf_sintetico(1))
    if db.get(Cidadao, cid) is None:
        db.add(Cidadao(cidadao_id=cid, nome="ZZTESTE ALUNO UM", data_nascimento=date(2015, 3, 1)))
        db.commit()
    antes = db.scalar(select(func.max(Auditoria.id))) or 0
    for corpo in ({"motivo": "x"}, {"motivo": "   "}, {}):
        r = client.post(f"/api/gold/cidadaos/{cid}/educacao/revelar", json=corpo, headers=tokens["admin_lgpd"])
        assert r.status_code == 400
    auds = list(db.scalars(select(Auditoria).where(Auditoria.id > antes, Auditoria.acao == "revelar_educacao_negado")))
    assert len(auds) == 3 and all(a.detalhe == {"perfil": "admin_lgpd", "razao": "motivo_invalido"} for a in auds)
    assert all(a.alvo == f"cidadao:{cid}" for a in auds)


def test_unidade_nome_situacao_e_hash_por_tabela(limpo):
    from app.models.camadas import EscolaMatricula, RegistroBronze
    db = limpo
    dados = origem()
    lei = LeitorMock(dados)
    sql = cq.ConsultasGE().pagina("ESC_UnidadeEscola", 5)[0]
    for col in ("uni_descricao", "uni_situacao", "uni_dataAlteracao"):
        assert f"[u].[{col}]" in sql
    assert "WITH (NOLOCK)" in sql
    assert _ciclo(db, lei, cfg(max_lotes=100)).status == "sucesso"
    m = db.scalar(select(EscolaMatricula).where(EscolaMatricula.alu_id == 1, EscolaMatricula.mtu_id == 2))
    assert (m.uni_id, m.unidade, m.unidade_situacao, m.unidade_excluida) == (1, "ZZTESTE UNIDADE SEDE", 1, False)
    assert m.escola == "ZZTESTE ESCOLA MUNICIPAL"

    def contagem():
        return dict(db.execute(select(RegistroBronze.tabela, func.count()).where(
            RegistroBronze.sistema_origem.in_(("GESTAOESCOLAR", "CORESSO"))).group_by(RegistroBronze.tabela)).all())
    antes = contagem()
    # unidade passa à situação 3: nova versão só na ESC_UnidadeEscola; matrícula marcada, nada apagado
    dados["ESC_UnidadeEscola"][0].update(uni_situacao=3, uni_dataAlteracao="2026-10-20T00:00:00")
    assert _ciclo(db, lei, cfg(max_lotes=100)).status == "sucesso"
    depois = contagem()
    assert {t for t in depois if depois[t] != antes.get(t)} == {"ESC_UnidadeEscola"}
    assert depois["ESC_UnidadeEscola"] == antes["ESC_UnidadeEscola"] + 1
    db.expire_all()
    mats = list(db.scalars(select(EscolaMatricula).where(EscolaMatricula.alu_id == 1)))
    assert len(mats) == 3 and all(x.unidade_excluida and x.unidade_situacao == 3 for x in mats)
    # sem descrição: unidade sem nome (a escola continua pelo esc_nome)
    dados["ESC_UnidadeEscola"][0].update(uni_descricao=None, uni_situacao=1, uni_dataAlteracao="2026-10-21T00:00:00")
    assert _ciclo(db, lei, cfg(max_lotes=100)).status == "sucesso"
    db.expire_all()
    m = db.scalar(select(EscolaMatricula).where(EscolaMatricula.alu_id == 1, EscolaMatricula.mtu_id == 2))
    assert m.unidade is None and not m.unidade_excluida and m.escola == "ZZTESTE ESCOLA MUNICIPAL"


def test_turma_sem_codigo_flui_ate_a_ficha(limpo, client, tokens):
    from app.models.camadas import Cidadao, EscolaMatricula
    db = limpo
    dados = origem()
    for t in dados["TUR_Turma"]:
        t["tur_codigo"] = None
    assert _ciclo(db, LeitorMock(dados), cfg(max_lotes=100)).status == "sucesso"
    mats = {m.mtu_id: m for m in db.scalars(select(EscolaMatricula).where(EscolaMatricula.alu_id == 1))}
    assert set(mats) == {1, 2, 3} and all(m.turma is None for m in mats.values()) and mats[2].atual
    cid = corr.cidadao_id_por_cpf(cpf_sintetico(1))
    if db.get(Cidadao, cid) is None:
        db.add(Cidadao(cidadao_id=cid, nome="ZZTESTE ALUNO UM", data_nascimento=date(2015, 3, 1)))
        db.commit()
    r = client.get(f"/api/gold/cidadaos/{cid}/educacao", headers=tokens["operador"])
    assert r.status_code == 200
    a = r.json()["alunos"][0]
    assert a["matricula_atual"]["turma"] is None and a["matricula_atual"]["escola"] == "ZZTESTE ESCOLA MUNICIPAL"


def test_ficha_ativo_inativo_e_excluido(limpo, client, tokens):
    """Situação 1 = ativo (atual); 5 = inativo, visível como histórico; 3 = exclusão lógica, fora da ficha."""
    from app.models.camadas import Cidadao, EscolaAluno
    db = limpo
    dados = origem()
    cpf = cpf_sintetico(1)
    # três alunos ZZTESTE do mesmo cidadão (mesmo CPF no CoreSSO); começam todos ativos e já vinculados
    for n, nome in ((21, "ZZTESTE FICHA ATIVO"), (22, "ZZTESTE FICHA INATIVO"), (23, "ZZTESTE FICHA EXCLUIDO")):
        dados["PES_Pessoa"].append({"pes_id": pid(n), "pes_nome": nome, "pes_nomeSocial": None,
                                    "pes_dataNascimento": "2000-01-01", "pes_sexo": 1, "pes_idFiliacaoMae": None,
                                    "pes_idFiliacaoPai": None, "pes_situacao": 1, "pes_dataAlteracao": "2026-10-01T10:00:00"})
        dados["PES_PessoaDocumento"].append({"pes_id": pid(n), "tdo_id": 1, "psd_numero": cpf, "psd_situacao": 1,
                                             "psd_dataAlteracao": "2026-10-01T10:00:00", "tdo_classificacao": 1})
        dados["ACA_Aluno"].append({"alu_id": n, "pes_id": pid(n), "end_id": None, "alu_situacao": 1,
                                   "alu_dataAlteracao": "2026-10-01T10:00:00"})
    lei = LeitorMock(dados)
    assert _ciclo(db, lei, cfg(max_lotes=100)).status == "sucesso"
    for a in dados["ACA_Aluno"]:
        if a["alu_id"] in (22, 23):
            a.update(alu_situacao=5 if a["alu_id"] == 22 else 3, alu_dataAlteracao="2026-10-20T10:00:00")
    assert _ciclo(db, lei, cfg(max_lotes=100)).status == "sucesso"
    db.expire_all()
    al = {a.alu_id: a for a in db.scalars(select(EscolaAluno).where(EscolaAluno.alu_id.in_((21, 22, 23))))}
    assert al[21].status_vinculo == "vinculado" and not al[21].excluido
    assert al[22].status_vinculo == "vinculado" and not al[22].excluido and al[22].situacao == 5
    assert al[23].status_vinculo == "excluido" and al[23].excluido  # linha mantida (sem DELETE)
    cid = corr.cidadao_id_por_cpf(cpf)
    if db.get(Cidadao, cid) is None:
        db.add(Cidadao(cidadao_id=cid, nome="ZZTESTE FICHA ATIVO", data_nascimento=date(2000, 1, 1)))
        db.commit()
    for perfil in ("operador", "admin_lgpd"):
        r = client.get(f"/api/gold/cidadaos/{cid}/educacao", headers=tokens[perfil])
        assert r.status_code == 200
        por_nome = {a["nome"]: a for a in r.json()["alunos"]}
        assert "ZZTESTE FICHA EXCLUIDO" not in por_nome and "ZZTESTE FICHA EXCLUIDO" not in r.text
        assert por_nome["ZZTESTE FICHA ATIVO"]["inativo"] is False and por_nome["ZZTESTE FICHA ATIVO"]["situacao"] == 1
        assert por_nome["ZZTESTE FICHA INATIVO"]["inativo"] is True and por_nome["ZZTESTE FICHA INATIVO"]["situacao"] == 5
        assert all(not a["excluido"] for a in por_nome.values())


def test_amostra_pode_rodar_de_dia(limpo):
    """Amostra (<= 50 linhas, NOLOCK, teto 1) não depende da janela; só a carga completa é recusada de dia."""
    import inspect

    from app.services import carga_gestaoescolar as cge
    assert "dentro_da_janela" not in inspect.getsource(cge.amostra)
    lei = LeitorMock(origem())
    assert cge.amostra(limpo, lei, cfg()).status == "sucesso"
    assert all(s.startswith("SELECT TOP (50) ") and "WITH (NOLOCK)" in s for s in lei.sqls)
    with pytest.raises(cge.CargaRecusada, match="janela"):
        cge.completa(limpo, lei, cfg(), agora=DIA)


# ---------------------------------------------------------------- SIGPMF_EDUCACAO_JANELA_NOTURNA
@pytest.fixture
def janela_env(monkeypatch):
    """Liga/desliga a trava pela env (None = env ausente) e limpa o cache de settings antes e depois."""
    from app.core.config import get_settings

    def _set(valor):
        if valor is None:
            monkeypatch.delenv("SIGPMF_EDUCACAO_JANELA_NOTURNA", raising=False)
        else:
            monkeypatch.setenv("SIGPMF_EDUCACAO_JANELA_NOTURNA", valor)
        get_settings.cache_clear()
    yield _set
    get_settings.cache_clear()


def test_janela_env_ausente_e_ligada_por_padrao(janela_env, limpo):
    from app.core.config import get_settings
    from app.services import carga_gestaoescolar as cge
    janela_env(None)
    assert get_settings().ge_janela_noturna is None and cge.janela_exigida() is True
    assert cge.amostra(limpo, LeitorMock(origem()), cfg()).status == "sucesso"
    with pytest.raises(cge.CargaRecusada) as e:
        cge.completa(limpo, LeitorMock(origem()), cfg(), agora=DIA)
    assert str(e.value) == cge.FORA_JANELA.format(janela=get_settings().agenda_janela)


@pytest.mark.parametrize("valor,exigida", [("false", False), ("FALSE", False), (" False ", False), ("true", True),
                                           ("", True), ("0", True), ("no", True), ("off", True), ("falso", True),
                                           ("xyz", True)])
def test_janela_so_false_explicito_desliga(janela_env, valor, exigida):
    from app.services import carga_gestaoescolar as cge
    janela_env(valor)
    assert cge.janela_exigida() is exigida


def test_janela_env_invalida_recusa_fora_da_janela(janela_env, limpo):
    from app.services import carga_gestaoescolar as cge
    janela_env("desligado")
    assert cge.amostra(limpo, LeitorMock(origem()), cfg()).status == "sucesso"
    with pytest.raises(cge.CargaRecusada, match="janela"):
        cge.completa(limpo, LeitorMock(origem()), cfg(), agora=DIA)


def test_completa_concorrente_recusada(janela_env, limpo):
    from app.models.controle import Execucao
    from app.services import carga_gestaoescolar as cge
    janela_env("false")
    lei = LeitorMock(origem())
    assert cge.amostra(limpo, lei, cfg()).status == "sucesso"
    rodando = Execucao(tipo=cge.TIPO_COMPLETA, sistema="GESTAOESCOLAR", status="em_andamento")
    limpo.add(rodando)
    limpo.commit()
    try:
        with pytest.raises(cge.CargaRecusada, match="em andamento"):
            cge.completa(limpo, lei, cfg(), agora=DIA)
    finally:
        rodando.status = "falha"
        limpo.commit()


def test_carga_so_le_fontes_do_catalogo(janela_env, limpo):
    from app.services import carga_gestaoescolar as cge
    janela_env("false")
    lei = LeitorMock(origem())
    assert cge.amostra(limpo, lei, cfg()).status == "sucesso"
    lei.sqls.clear()
    cge.completa(limpo, lei, cfg(lote=1000, max_lotes=50), agora=DIA)
    permitidas = ({f.tabela for f in cq.FONTES} | {j[1] for f in cq.FONTES for j in f.juncoes}
                  | {"ACA_Aluno", "ACA_AlunoResponsavel", "PES_Pessoa"})  # escopo da escola
    for sql in lei.sqls:
        tabs = set(re.findall(r"\]\.\[dbo\]\.\[([A-Za-z0-9_]+)\]", sql))
        assert tabs and tabs <= permitidas, tabs
    assert len(lei.sqls) >= len(cq.FONTES)


def test_janela_env_ligada_fora_da_janela_recusa_igual_a_hoje(janela_env, limpo):
    from app.core.config import get_settings
    from app.models.controle import Execucao
    from app.services import carga_gestaoescolar as cge
    janela_env("true")
    assert cge.amostra(limpo, LeitorMock(origem()), cfg()).status == "sucesso"
    antes = limpo.scalar(select(func.count()).select_from(Execucao).where(Execucao.tipo == cge.TIPO_COMPLETA))
    with pytest.raises(cge.CargaRecusada) as e:
        cge.completa(limpo, LeitorMock(origem()), cfg(), agora=DIA)
    assert str(e.value) == cge.FORA_JANELA.format(janela=get_settings().agenda_janela)
    depois = limpo.scalar(select(func.count()).select_from(Execucao).where(Execucao.tipo == cge.TIPO_COMPLETA))
    assert antes == depois  # nada iniciado


def test_janela_env_desligada_fora_da_janela_inicia_e_registra_no_log(janela_env, limpo):
    from app.models.controle import Log
    from app.services import carga_gestaoescolar as cge
    janela_env("false")
    lei = LeitorMock(origem())
    assert cge.amostra(limpo, lei, cfg()).status == "sucesso"
    ex = cge.completa(limpo, lei, cfg(lote=2, max_lotes=1), agora=DIA)
    assert ex.status == "parcial" and ex.lotes == 1  # lote e teto inalterados
    msgs = list(limpo.scalars(select(Log.mensagem).where(Log.execucao_id == ex.id)))
    assert any("Janela noturna ignorada" in m for m in msgs)
    # trava ligada: nenhuma linha de "janela ignorada" no log
    janela_env("true")
    ex2 = cge.completa(limpo, lei, cfg(lote=2, max_lotes=1), agora=NOITE)
    assert not [m for m in limpo.scalars(select(Log.mensagem).where(Log.execucao_id == ex2.id)) if "ignorada" in m]


def test_janela_env_desligada_ainda_exige_amostra(janela_env, limpo):
    from app.models.controle import Execucao
    from app.services import carga_gestaoescolar as cge
    janela_env("false")
    limpo.add(Execucao(tipo=cge.TIPO_AMOSTRA, sistema="GESTAOESCOLAR", status="falha"))
    limpo.commit()
    with pytest.raises(cge.CargaRecusada, match="amostra"):
        cge.completa(limpo, LeitorMock(origem()), cfg(), agora=DIA)


class _LeitorRota(LeitorMock):
    def __init__(self, _cfg):
        super().__init__(origem())

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _rota_prep(monkeypatch, janela_env, valor):
    from app.services import carga_gestaoescolar as cge
    monkeypatch.setenv("SIGPMF_SRC_GESTAOESCOLAR_HOST", "zz-host-teste")
    monkeypatch.setenv("SIGPMF_SRC_GESTAOESCOLAR_USER", "zz_leitor_teste")
    monkeypatch.setenv("SIGPMF_SRC_GESTAOESCOLAR_PASSWORD", "zz-senha-teste")
    monkeypatch.setattr(cq, "LeitorGE", _LeitorRota)  # nunca abre conexão real
    monkeypatch.setattr(cge, "dentro_da_janela", lambda agora: (False, "00:00-06:00"))  # sempre "fora da janela"
    janela_env(valor)


def _ultima_auditoria(db, acao):
    from app.models.controle import Auditoria
    db.expire_all()
    return db.scalar(select(Auditoria).where(Auditoria.acao == acao).order_by(Auditoria.id.desc()).limit(1))


def test_rota_env_ligada_fora_da_janela_409_e_auditada(monkeypatch, janela_env, limpo, client, tokens):
    _rota_prep(monkeypatch, janela_env, "true")
    assert client.post("/api/educacao/conector/amostra", headers=tokens["admin"]).status_code == 200
    r = client.post("/api/educacao/conector/completa", headers=tokens["admin"])
    assert r.status_code == 409 and "janela noturna" in r.json()["detail"]
    a = _ultima_auditoria(limpo, "gestaoescolar_negado")
    assert a and "janela" in a.detalhe["motivo"]


def test_rota_env_desligada_fora_da_janela_inicia_admin_amostra_e_auditoria(monkeypatch, janela_env, limpo, client, tokens):
    from app.models.controle import Execucao
    from app.services import carga_gestaoescolar as cge
    _rota_prep(monkeypatch, janela_env, "false")
    # ADMIN continua obrigatório
    for perfil in ("operador", "leitura"):
        assert client.post("/api/educacao/conector/completa", headers=tokens[perfil]).status_code == 403
    # amostra continua obrigatória (e a recusa é auditada)
    limpo.add(Execucao(tipo=cge.TIPO_AMOSTRA, sistema="GESTAOESCOLAR", status="falha"))
    limpo.commit()
    r = client.post("/api/educacao/conector/completa", headers=tokens["admin"])
    assert r.status_code == 409 and "amostra" in r.json()["detail"]
    assert "amostra" in _ultima_auditoria(limpo, "gestaoescolar_negado").detalhe["motivo"]
    # com amostra ok, fora da janela, a carga começa e a auditoria registra que a janela foi ignorada
    assert client.post("/api/educacao/conector/amostra", headers=tokens["admin"]).status_code == 200
    r = client.post("/api/educacao/conector/completa", headers=tokens["admin"])
    assert r.status_code == 200, r.text
    assert r.json()["status"] in ("parcial", "sucesso") and r.json()["lotes"] >= 1
    a = _ultima_auditoria(limpo, "gestaoescolar_completa")
    assert a.alvo == f"execucao:{r.json()['execucao_id']}" and a.detalhe["janela_ignorada"] is True
    assert a.detalhe["fora_da_janela"] is True


def test_rota_completa_concorrente_409(monkeypatch, janela_env, limpo, client, tokens):
    from app.models.controle import Execucao
    from app.services import carga_gestaoescolar as cge
    _rota_prep(monkeypatch, janela_env, "false")
    assert client.post("/api/educacao/conector/amostra", headers=tokens["admin"]).status_code == 200
    rodando = Execucao(tipo=cge.TIPO_COMPLETA, sistema="GESTAOESCOLAR", status="em_andamento")
    limpo.add(rodando)
    limpo.commit()
    try:
        r = client.post("/api/educacao/conector/completa", headers=tokens["admin"])
        assert r.status_code == 409 and "em andamento" in r.json()["detail"]
        assert "em andamento" in _ultima_auditoria(limpo, "gestaoescolar_negado").detalhe["motivo"]
    finally:
        rodando.status = "falha"
        limpo.commit()


# ---------------------------------------------------------------- cancelar e execução órfã
class _LeitorCancela(LeitorMock):
    """Pede o cancelamento (outra sessão, como a rota faria) durante a leitura do 2º lote."""

    def __init__(self, dados):
        super().__init__(dados)
        self.leituras = 0

    def ler(self, f, ultima, limite):
        self.leituras += 1
        if self.leituras == 2:
            from app.core.db import SessionLocal
            from app.services import carga_gestaoescolar as cge
            with SessionLocal() as outra:
                cge.solicita_cancelamento(outra)
        return super().ler(f, ultima, limite)


def test_cancelar_para_no_fim_do_lote_e_libera_nova_carga(janela_env, limpo):
    from app.models.controle import CheckpointCarga, Execucao
    from app.services import carga_gestaoescolar as cge
    janela_env("false")
    lei = _LeitorCancela(origem())
    assert cge.amostra(limpo, LeitorMock(origem()), cfg()).status == "sucesso"
    ex = cge.completa(limpo, lei, cfg(lote=1, max_lotes=50), agora=DIA)
    assert ex.status == "cancelada" and ex.lotes == 2 and lei.leituras == 2  # termina o lote em curso e para
    assert ex.atualizado_em is not None  # batida por lote
    ck = limpo.scalar(select(CheckpointCarga).where(CheckpointCarga.sistema == "GESTAOESCOLAR",
                                                    CheckpointCarga.tabela == "ACA_Aluno"))
    assert ck.posicao == 2  # o já gravado fica
    assert not cge.completa_em_andamento(limpo)
    nova = cge.completa(limpo, LeitorMock(origem()), cfg(lote=1, max_lotes=1), agora=DIA)
    assert nova.status == "parcial" and limpo.get(Execucao, nova.id).cancelar is False


def _rodando(db, minutos_sem_batida):
    from datetime import timedelta

    from app.models.controle import Execucao
    from app.services import carga_gestaoescolar as cge
    ex = Execucao(tipo=cge.TIPO_COMPLETA, sistema="GESTAOESCOLAR", status="em_andamento",
                  atualizado_em=datetime.now(UTC) - timedelta(minutes=minutos_sem_batida))
    db.add(ex)
    db.commit()
    return ex


def test_rota_cancelar_admin_409_e_auditoria(monkeypatch, janela_env, limpo, client, tokens):
    _rota_prep(monkeypatch, janela_env, "false")
    for perfil in ("operador", "leitura", "admin_lgpd"):
        assert client.post("/api/educacao/conector/cancelar", headers=tokens[perfil]).status_code == 403
    r = client.post("/api/educacao/conector/cancelar", headers=tokens["admin"])
    assert r.status_code == 409 and "Nenhuma" in r.json()["detail"]
    assert _ultima_auditoria(limpo, "gestaoescolar_cancelar_negado") is not None
    ex = _rodando(limpo, 0)
    try:
        r = client.post("/api/educacao/conector/cancelar", headers=tokens["admin"])
        assert r.status_code == 200 and r.json()["execucao_id"] == ex.id
        limpo.refresh(ex)
        assert ex.cancelar is True
        assert _ultima_auditoria(limpo, "gestaoescolar_cancelar").alvo == f"execucao:{ex.id}"
    finally:
        ex.status = "falha"
        limpo.commit()


def test_rota_marcar_falha_orfa(monkeypatch, janela_env, limpo, client, tokens):
    _rota_prep(monkeypatch, janela_env, "false")
    monkeypatch.setenv("SIGPMF_EDUCACAO_ORFA_MIN", "30")
    janela_env("false")
    recente = _rodando(limpo, 5)
    url = f"/api/educacao/conector/execucoes/{recente.id}/marcar-falha"
    assert client.post(url, headers=tokens["operador"]).status_code == 403
    r = client.post(url, headers=tokens["admin"])
    assert r.status_code == 409 and "30 min" in r.json()["detail"]
    assert _ultima_auditoria(limpo, "gestaoescolar_marcar_falha_negado").detalhe["motivo"] == "batida_recente"
    recente.status = "falha"
    limpo.commit()
    velha = _rodando(limpo, 45)
    # ainda em andamento: nova carga recusada
    assert client.post("/api/educacao/conector/amostra", headers=tokens["admin"]).status_code == 200
    assert client.post("/api/educacao/conector/completa", headers=tokens["admin"]).status_code == 409
    r = client.post(f"/api/educacao/conector/execucoes/{velha.id}/marcar-falha", headers=tokens["admin"])
    assert r.status_code == 200 and r.json()["status"] == "falha"
    limpo.refresh(velha)
    assert velha.status == "falha" and velha.finalizado_em is not None
    assert _ultima_auditoria(limpo, "gestaoescolar_marcar_falha").alvo == f"execucao:{velha.id}"
    # de novo: já não está em andamento -> 409 auditado
    r = client.post(f"/api/educacao/conector/execucoes/{velha.id}/marcar-falha", headers=tokens["admin"])
    assert r.status_code == 409
    assert _ultima_auditoria(limpo, "gestaoescolar_marcar_falha_negado").detalhe["motivo"] == "nao_em_andamento"
    # trava de concorrência liberada
    r = client.post("/api/educacao/conector/completa", headers=tokens["admin"])
    assert r.status_code == 200, r.text
    assert client.post("/api/educacao/conector/execucoes/999999/marcar-falha", headers=tokens["admin"]).status_code == 404
