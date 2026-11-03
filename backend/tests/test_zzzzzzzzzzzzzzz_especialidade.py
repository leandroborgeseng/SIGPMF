"""ES1–ES3: especialidade sem nome no dicionário da Saúde (Aux_Especialidades tem só parte dos códigos em uso).

ES1: nunca inventar nome ("Especialidade 34" parecia um nome real); código sem nome aparece como tal.
ES2: o ouro mede a % de atendimentos/agendamentos sem nome e lista os códigos sem nome por tabela de origem (sem pessoa).
ES3: grupo com menos de 5 casos não sai na lista de códigos.
"""
from datetime import date

from sqlalchemy import select

from app.gold import indicadores as gi
from app.models.camadas import Agendamento, Atendimento, Indicador
from app.silver.fatos import rotulo_especialidade


def test_es1_rotulo_nao_inventa_nome():
    dic = {2: "Oftalmologia"}
    assert rotulo_especialidade(2, dic) == "Oftalmologia"
    assert rotulo_especialidade(34, dic) == "Código 34 (sem nome no cadastro)"
    assert rotulo_especialidade(None, dic) == "Sem especialidade informada"
    assert "Especialidade 34" not in rotulo_especialidade(34, {})


def test_es2_es3_ouro_mede_codigos_sem_nome(db, monkeypatch):
    monkeypatch.setattr(gi, "nomes", lambda _db: {"vacinas": {}, "especialidades": {2: "Oftalmologia"}})
    try:
        for m in (Atendimento, Agendamento):
            db.query(m).delete()
        hoje = date.today()
        db.add_all([Atendimento(chave_origem=f"es-at-{i}", data=hoje, unidade=1, especialidade=2 if i < 4 else 34) for i in range(10)])
        db.add_all([Agendamento(chave_origem=f"es-ag-{i}", data_consulta=hoje, data_marcacao=hoje, dias_espera=10, unidade=1,
                                especialidade=125 if i < 6 else 2 if i < 9 else 777) for i in range(10)])
        db.flush()
        gi.reconstruir(db)
        ind = {(i.serie, i.dim): i.valor for i in db.scalars(select(Indicador))}
        assert ind[("saude_especialidade_sem_nome", "atendimentos")] == 60.0
        assert ind[("saude_especialidade_sem_nome", "agendamentos")] == 70.0
        assert ind[("saude_especialidade_codigo_sem_nome", "Consultas · código 34")] == 6
        assert ind[("saude_especialidade_codigo_sem_nome", "Agendamento_Consulta · código 125")] == 6
        assert ("saude_especialidade_codigo_sem_nome", "Agendamento_Consulta · código 777") not in ind  # ES3: 1 caso
        assert ind[("saude_agend_especialidade", "Código 125 (sem nome no cadastro)")] == 6
        assert not any(d.startswith("Especialidade ") for (_s, d) in ind)
    finally:
        db.rollback()
