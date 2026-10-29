"""PES_PessoaDocumento (tipo de documento pelo catálogo, planos A/B) e aviso de tabela vazia em todas as fontes.

Só dados sintéticos ZZTESTE; CPFs com prefixo 999000 (DV válido, de ninguém). Nenhuma origem real.
"""
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from app.connectors.gestaoescolar import consultas as cq
from app.models.controle import Auditoria, CheckpointCarga, Execucao, Log
from app.services import carga_completa as ccomp
from app.services import carga_conector as cc
from app.services import carga_gestaoescolar as cge
from app.services import tabelas_vazias
from tests.test_zzzzzzzzzzzzzzzzzzz_gestaoescolar import NOITE, LeitorMock, _ciclo, cfg, cpf_sintetico, origem, pid
from tests.test_zzzzzzzzzzzzzzzzzzz_gestaoescolar import janela_env as janela_env  # noqa: F401 (fixture)
from tests.test_zzzzzzzzzzzzzzzzzzz_gestaoescolar import limpo as limpo  # noqa: F401 (fixture)


def guid(n: int) -> str:
    return str(uuid.UUID(int=0x3333_0000_0000_0000_0000_0000_0000_0000 + n)).upper()


# Mesmo formato do catálogo da origem em 29/10/2026: 7 tipos, todos com classificação 99, um com sigla vazia.
CATALOGO_99 = [{"tdo_id": guid(i), "tdo_classificacao": 99, "tdo_sigla": s, "tdo_validacao": 1 if s == "CPF" else 2}
               for i, s in enumerate(("CNPJ", "CPF", "RG", "NIS", "CIN", "CNS", ""), start=1)]


CNS_ZZ = "799990000000017"  # sintético (prefixo 7999...), não é de ninguém; tipo CNS fica fora da carga


# ---------------------------------------------------------------- regras do tipo (planos A e B)
@pytest.mark.parametrize("cl,esperado", [(1, 1), (3, 3), (4, 4), (2, None), (5, None), (6, None), (10, None), (99, None)])
def test_plano_a_classificacao_1_3_4_aceitas_outras_fora(cl, esperado):
    assert cq.classe_do_tipo({"tdo_classificacao": cl, "tdo_sigla": "RG"}) == esperado  # sigla RG nunca entra


@pytest.mark.parametrize("cl", [None, 99, 2, 5, 10])  # fora de 1/3/4 a classificação é ignorada: decide a sigla
@pytest.mark.parametrize("sigla,esperado", [
    ("CPF", 1), (" cpf ", 1), ("Cpf", 1), ("NIS", 4), (" nis", 4), ("PIS", 3), ("pis/pasep", 3),
    ("CNS", None), ("cns ", None), ("CIN", None), ("RG", None), ("CNH", None), ("CNPJ", None), ("", None), (None, None),
    ("   ", None)])
def test_plano_b_sigla_cpf_nis_pis(cl, sigla, esperado):
    assert cq.classe_do_tipo({"tdo_classificacao": cl, "tdo_sigla": sigla, "tdo_validacao": 1}) == esperado


def test_catalogo_real_todo_99_aceita_so_cpf_e_nis():
    ids, mapa = cq.mapa_tipos(CATALOGO_99)
    assert ids == (guid(2), guid(4))
    assert mapa == {cq.chave_tipo(guid(2)): 1, cq.chave_tipo(guid(4)): 4}  # CNPJ, RG, CIN, CNS e vazia fora


def test_catalogo_misto_planos_a_e_b_juntos():
    misto = [{"tdo_id": guid(11), "tdo_classificacao": 1, "tdo_sigla": "DOC1"},
             {"tdo_id": guid(13), "tdo_classificacao": 3, "tdo_sigla": None},
             {"tdo_id": guid(14), "tdo_classificacao": 4, "tdo_sigla": " "},
             {"tdo_id": guid(12), "tdo_classificacao": 2, "tdo_sigla": "RG"},
             *CATALOGO_99]
    ids, mapa = cq.mapa_tipos(misto)
    assert set(ids) == {guid(11), guid(13), guid(14), guid(2), guid(4)}
    assert mapa[cq.chave_tipo(guid(11))] == 1 and mapa[cq.chave_tipo(guid(13))] == 3 and mapa[cq.chave_tipo(guid(14))] == 4
    assert all(cq.chave_tipo(guid(n)) not in mapa for n in (12, 1, 3, 5, 6, 7))


def test_catalogo_sem_tipo_aceito_falha_com_motivo_fixo():
    with pytest.raises(cq.TiposDocumentoAusentes) as e:
        cq.mapa_tipos([t for t in CATALOGO_99 if t["tdo_sigla"] not in ("CPF", "NIS")])
    assert e.value.codigo == "sem_tipo_documento" and str(e.value) == cq.MSG_SEM_TIPO_DOCUMENTO


# ---------------------------------------------------------------- leitor real: catálogo primeiro, GUIDs só como parâmetro
class _Cur:
    def __init__(self, o):
        self.o, self.res = o, []

    def execute(self, sql, params=None):
        self.o.chamadas.append((sql, params))
        if "[SYS_TipoDocumentacao]" in sql:
            self.res = [dict(t) for t in self.o.catalogo]
        else:
            aceitos = {str(p) for p in (params or ())}
            self.res = [dict(r) for r in self.o.docs if r["tdo_id"] in aceitos]

    def fetchmany(self, n):
        return self.res[:n]


class _Conn:
    def __init__(self, catalogo, docs):
        self.catalogo, self.docs, self.chamadas = catalogo, docs, []

    def cursor(self, as_dict=False):
        return _Cur(self)

    def rollback(self):
        pass


def test_leitor_le_catalogo_uma_vez_e_filtra_por_parametros():
    docs = [{"pes_id": pid(1), "tdo_id": guid(2), "psd_numero": cpf_sintetico(1), "psd_situacao": 1},
            {"pes_id": pid(1), "tdo_id": guid(3), "psd_numero": "ZZ-RG-1", "psd_situacao": 1},
            {"pes_id": pid(2), "tdo_id": guid(4), "psd_numero": "99900000002", "psd_situacao": 1},
            {"pes_id": pid(2), "tdo_id": guid(6), "psd_numero": CNS_ZZ, "psd_situacao": 1}]
    lt = cq.LeitorGE(cfg())
    lt._conn = _Conn(CATALOGO_99, docs)
    f = cq.POR_NOME["PES_PessoaDocumento"]
    linhas = lt.ler(f, None, 10)
    lt.ler(f, None, 10)
    assert sum("[SYS_TipoDocumentacao]" in s for s, _ in lt._conn.chamadas) == 1  # catálogo uma vez por conexão
    sql, params = lt._conn.chamadas[1]
    assert "[d].[tdo_id] IN (%s, %s)" in sql and params == (guid(2), guid(4))
    for s, _p in lt._conn.chamadas:  # nenhum GUID nem número concatenado no SQL
        assert guid(2) not in s and guid(2).lower() not in s and cpf_sintetico(1) not in s and CNS_ZZ not in s
    assert sorted((r["tdo_classe"], r["psd_numero"]) for r in linhas) == [(1, cpf_sintetico(1)), (4, "99900000002")]


def test_leitor_sem_tipo_aceito_falha_antes_de_ler_documentos():
    lt = cq.LeitorGE(cfg())
    lt._conn = _Conn([t for t in CATALOGO_99 if t["tdo_sigla"] in ("RG", "CIN", "CNPJ", "")], [])
    with pytest.raises(cq.TiposDocumentoAusentes):
        lt.ler(cq.POR_NOME["PES_PessoaDocumento"], None, 10)
    assert len(lt._conn.chamadas) == 1  # só o catálogo


# ---------------------------------------------------------------- carga da educação
def _dados_99():
    d = origem()
    tipo = {1: guid(2), 4: guid(4)}
    d["PES_PessoaDocumento"] = [{**r, "tdo_id": tipo[r["tdo_id"]]} for r in d["PES_PessoaDocumento"]] + [
        {"pes_id": pid(2), "tdo_id": guid(6), "psd_numero": CNS_ZZ, "psd_situacao": 1,
         "psd_dataAlteracao": "2026-10-01T10:00:00"},
        {"pes_id": pid(3), "tdo_id": guid(3), "psd_numero": "ZZRG0003", "psd_situacao": 1,  # RG: fica de fora
         "psd_dataAlteracao": "2026-10-01T10:00:00"}]
    return d


def test_catalogo_99_cpf_e_nis_chegam_na_prata_sem_vazar(janela_env, limpo):
    from app.identity import correlacao as corr
    from app.models.camadas import EscolaAluno
    db = limpo
    janela_env(None)
    ex = _ciclo(db, LeitorMock(_dados_99(), CATALOGO_99), cfg(max_lotes=100))
    assert ex.status == "sucesso", ex.mensagem
    al = {a.alu_id: a for a in db.scalars(select(EscolaAluno))}
    assert al[1].cpf == cpf_sintetico(1) and al[1].cidadao_id == corr.cidadao_id_por_cpf(cpf_sintetico(1))
    assert al[2].nis == "99900000002"
    assert tabelas_vazias.alerta(ex) is None
    # LGPD: CPF/NIS nunca em log, mensagem nem auditoria
    textos = " ".join(m for m in db.scalars(select(Log.mensagem)) if m) + " " + (ex.mensagem or "")
    textos += " ".join(str(a) for a in db.scalars(select(Auditoria.detalhe)))
    assert cpf_sintetico(1) not in textos and "99900000002" not in textos and CNS_ZZ not in textos
    from app.models.camadas import RegistroBronze
    docs = [r.dados for r in db.scalars(select(RegistroBronze).where(RegistroBronze.tabela == "PES_PessoaDocumento"))]
    assert sorted(x["tdo_classe"] for x in docs) == [1, 1, 4]  # CNS e RG não entram


def test_api_sem_admin_lgpd_nao_mostra_cpf_nem_cns(janela_env, limpo, client, tokens):
    from datetime import date

    from app.identity import correlacao as corr
    from app.models.camadas import Cidadao
    db = limpo
    janela_env(None)
    ex = _ciclo(db, LeitorMock(_dados_99(), CATALOGO_99), cfg(max_lotes=100))
    cid = corr.cidadao_id_por_cpf(cpf_sintetico(1))
    if db.get(Cidadao, cid) is None:
        db.add(Cidadao(cidadao_id=cid, nome="ZZTESTE ALUNO UM", data_nascimento=date(2015, 3, 1)))
        db.commit()
    for perfil in ("leitura", "operador", "admin"):
        r = client.get(f"/api/gold/cidadaos/{cid}/educacao", headers=tokens[perfil])
        assert r.status_code in (200, 403), r.text
        assert cpf_sintetico(1) not in r.text and CNS_ZZ not in r.text, perfil
        lg = client.get(f"/api/execucoes/{ex.id}/logs", headers=tokens[perfil])
        assert cpf_sintetico(1) not in lg.text and CNS_ZZ not in lg.text, perfil


def test_completa_sem_tipo_aceito_falha_com_codigo_fixo(janela_env, limpo):
    db = limpo
    janela_env(None)
    sem = [t for t in CATALOGO_99 if t["tdo_sigla"] not in ("CPF", "NIS")]
    lm = LeitorMock(_dados_99(), CATALOGO_99)
    a = cge.amostra(db, lm, cfg(), "zz@aion.local")
    assert a.status == "sucesso"
    lm.catalogo = sem
    ex = None
    for _ in range(50):
        ex = cge.completa(db, lm, cfg(max_lotes=100), "zz@aion.local", agora=NOITE)
        if ex.status != "parcial":
            break
    assert ex.status == "falha" and ex.erro_classe == "sem_tipo_documento"
    assert cq.MSG_SEM_TIPO_DOCUMENTO in ex.mensagem and "TiposDocumentoAusentes" not in ex.mensagem
    assert ex.erro_publico()["motivo"] == cq.MSG_SEM_TIPO_DOCUMENTO
    a2 = cge.amostra(db, lm, cfg(), "zz@aion.local")
    assert a2.status == "falha" and a2.erro_classe == "sem_tipo_documento" and cq.MSG_SEM_TIPO_DOCUMENTO in a2.mensagem


def test_educacao_fonte_vazia_gera_warning_e_alerta(janela_env, limpo):
    db = limpo
    janela_env(None)
    d = _dados_99()
    d["PES_PessoaDocumento"] = []
    ex = _ciclo(db, LeitorMock(d, CATALOGO_99), cfg(max_lotes=100))
    assert ex.status == "sucesso"  # semântica de sucesso mantida (agenda/assinatura), com código de alerta
    assert ex.erro_classe == "tabela_vazia" and ex.alerta_publico()["tabelas"] == ["PES_PessoaDocumento"]
    assert "Atenção: 1 tabela(s)" in ex.mensagem and ex.erro_publico() is None
    warns = list(db.scalars(select(Log.mensagem).where(Log.execucao_id == ex.id, Log.nivel == "WARNING")))
    assert any("[tabela_vazia]" in m and "PES_PessoaDocumento" in m for m in warns)


def test_releitura_so_de_pessoadocumento(janela_env, limpo):
    from app.models.camadas import EscolaAluno, RegistroBronze
    db = limpo
    janela_env(None)
    d = _dados_99()
    vazio = {**d, "PES_PessoaDocumento": []}
    _ciclo(db, LeitorMock(vazio, CATALOGO_99), cfg(max_lotes=100))  # como na produção: documentos com 0

    def contagens():
        return dict(db.execute(select(RegistroBronze.tabela, func.count()).where(
            RegistroBronze.sistema_origem.in_(("GESTAOESCOLAR", "CORESSO"))).group_by(RegistroBronze.tabela)).all())
    antes = contagens()
    r = cge.prepara_releitura(db, "PES_PessoaDocumento", "zz@aion.local")
    assert r == {"fonte": "PES_PessoaDocumento", "outras_concluidas": 12}
    cks = {c.tabela: c for c in db.scalars(select(CheckpointCarga).where(CheckpointCarga.sistema.in_(("GESTAOESCOLAR", "CORESSO"))))}
    assert not cks["PES_PessoaDocumento"].concluida and cks["PES_PessoaDocumento"].posicao == 0
    assert all(c.concluida for t, c in cks.items() if t != "PES_PessoaDocumento")
    lm = LeitorMock(d, CATALOGO_99)
    ex = cge.completa(db, lm, cfg(max_lotes=100), "zz@aion.local", agora=NOITE)
    assert ex.status == "sucesso" and ex.erro_classe is None, ex.mensagem  # sem falso alerta das outras 12
    principais = {s.split(" FROM ", 1)[1].split(" AS ")[0] for s in lm.sqls}
    assert principais == {"[FRA_CoreSSO].[dbo].[PES_PessoaDocumento]"}
    depois = contagens()
    assert {t: n for t, n in depois.items() if t != "PES_PessoaDocumento"} == \
        {t: n for t, n in antes.items() if t != "PES_PessoaDocumento"}
    assert depois["PES_PessoaDocumento"] == len(d["PES_PessoaDocumento"]) - 2  # CNS e RG não entram
    db.expire_all()
    al = {a.alu_id: a for a in db.scalars(select(EscolaAluno))}
    assert al[1].cpf == cpf_sintetico(1)  # prata reprocessada
    aud = db.scalar(select(Auditoria).where(Auditoria.acao == "educacao_releitura_fonte").order_by(Auditoria.id.desc()))
    assert aud.usuario == "zz@aion.local" and cpf_sintetico(1) not in str(aud.detalhe)


def test_releitura_recusa_fonte_desconhecida(limpo):
    with pytest.raises(cge.CargaRecusada):
        cge.prepara_releitura(limpo, "ZZ_Inexistente", "zz@aion.local")


# ---------------------------------------------------------------- tabela vazia em todas as fontes do motor genérico
@pytest.mark.parametrize("conector", ccomp.CONECTORES_COMPLETA)
def test_tabela_vazia_warning_por_fonte(db, conector):
    sistema = cc.CONECTORES[conector][0]
    tabelas = cc.tabelas_do_conector(sistema)
    assert tabelas, conector
    ex = Execucao(tipo=ccomp.TIPO, sistema=sistema, status="em_andamento", usuario="zz@aion.local", modo="completa")
    db.add(ex)
    db.commit()
    cps = {t: SimpleNamespace(concluida=True, posicao=0 if i == 0 else 5) for i, t in enumerate(tabelas)}
    vazias = ccomp.avisa_tabelas_vazias(db, ex, sistema, cps)
    db.commit()
    assert vazias == [tabelas[0]]
    assert ex.erro_classe == "tabela_vazia" and ex.alerta_publico()["tabelas"] == [tabelas[0]]
    w = list(db.scalars(select(Log.mensagem).where(Log.execucao_id == ex.id, Log.nivel == "WARN")))
    assert len(w) == 1 and "[tabela_vazia]" in w[0] and tabelas[0] in w[0]
    # tudo com linhas: nenhum aviso
    ex2 = Execucao(tipo=ccomp.TIPO, sistema=sistema, status="em_andamento", usuario="zz@aion.local", modo="completa")
    db.add(ex2)
    db.commit()
    assert ccomp.avisa_tabelas_vazias(db, ex2, sistema, {t: SimpleNamespace(concluida=True, posicao=1) for t in tabelas}) == []
    assert ex2.erro_classe is None and ex2.alerta_publico() is None


def test_saude_carga_completa_com_tabela_vazia_fica_sucesso_com_alerta(client, tokens, db, monkeypatch):
    import tests.test_zzz_carga_completa as tcc
    from tests.test_zzz_carga_completa import _amostra, _ultima
    o = {"SAUDE": tcc.Origem("SAUDE"), "CEMITERIO": tcc.Origem("CEMITERIO")}
    monkeypatch.setenv("CARGA_COMPLETA_LOTE", "2")
    monkeypatch.setenv("CARGA_COMPLETA_PAUSA_MS", "0")
    monkeypatch.setattr(tcc.recursos, "disco", lambda: tcc.recursos.Uso(total=100, livre=50))
    from app.core.config import get_settings
    get_settings.cache_clear()
    monkeypatch.setattr(ccomp, "leitor_padrao", lambda conector, db: o[cc.CONECTORES[conector][0]])
    monkeypatch.setattr(ccomp, "disparar", lambda ex_id, conector, trava, somente_camadas=False:
                        ccomp.rodar(ex_id, conector, trava, leitor=o[cc.CONECTORES[conector][0]],
                                    somente_camadas=somente_camadas))
    original = o["SAUDE"].linhas
    monkeypatch.setattr(o["SAUDE"], "linhas", lambda t: [] if t == "Aux_Vacina" else original(t))
    db.query(CheckpointCarga).filter(CheckpointCarga.sistema == "SAUDE").delete()
    db.commit()
    try:
        _amostra(db, "saude")
        r = client.post("/api/conectores/saude/completa", headers=tokens["admin"])
        assert r.status_code == 202, r.text
        ex = _ultima(db, "SAUDE")
        assert ex.status == "sucesso", ex.mensagem
        assert ex.erro_classe == "tabela_vazia" and "Aux_Vacina" in ex.erro_tabela and "Atenção" in ex.mensagem
        s = client.get("/api/conectores/saude/completa", headers=tokens["admin"]).json()
        assert s["execucao"]["alerta"]["codigo"] == "tabela_vazia" and "Aux_Vacina" in s["execucao"]["alerta"]["tabelas"]
    finally:
        get_settings.cache_clear()
