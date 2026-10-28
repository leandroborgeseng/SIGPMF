"""R2 do QA (#22): a prata grava em lotes de 5 mil (nunca um INSERT por linha nem tudo de uma vez), e a medição por
etapa só registra nome, tempo, contagens e RSS (sem PII). Dados sintéticos ZZTESTE, transação desfeita no fim."""
import logging

from sqlalchemy import event, func, select

from app.models.camadas import Inconsistencia, RegistroBronze
from app.models.controle import Execucao
from app.silver import processamento


def _conta_inserts(db, tabela):
    lotes = []

    def antes(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("INSERT") and f"silver.{tabela}" in statement:
            lotes.append(len(parameters) if executemany else 1)
    eng = db.get_bind()
    event.listen(eng, "before_cursor_execute", antes)
    return lotes, lambda: event.remove(eng, "before_cursor_execute", antes)


def test_grava_em_lotes_de_5000(db):
    n = 12_001
    lotes, solta = _conta_inserts(db, "inconsistencias")
    try:
        antes = db.scalar(select(func.count()).select_from(Inconsistencia))
        objs = (Inconsistencia(sistema="ZZTESTE", tabela="zz", chave_origem=f"ZZ{i}", tipo="zzteste") for i in range(n))
        processamento._grava(db, Inconsistencia, objs)
        assert lotes == [5000, 5000, 2001]
        assert db.scalar(select(func.count()).select_from(Inconsistencia)) - antes == n
        # atributo não definido (campo/detalhe/cidadao_id) fica de fora e cai no default (NULL)
        assert db.scalar(select(func.count()).select_from(Inconsistencia).where(
            Inconsistencia.sistema == "ZZTESTE", Inconsistencia.campo.is_(None))) == n
    finally:
        solta()
        db.rollback()


def test_grava_lote_exato_e_vazio(db):
    lotes, solta = _conta_inserts(db, "inconsistencias")
    try:
        processamento._grava(db, Inconsistencia, [])
        assert lotes == []
        processamento._grava(db, Inconsistencia, (Inconsistencia(sistema="ZZTESTE", tabela="zz", chave_origem=str(i),
                                                                 tipo="zz") for i in range(5000)))
        assert lotes == [5000]  # sem um INSERT vazio sobrando no fim
    finally:
        solta()
        db.rollback()


def test_medicao_por_etapa_sem_pii(db, caplog):
    ex = Execucao(tipo="zzteste_prata", sistema="SAUDE", status="sucesso", usuario="zzteste")
    db.add(ex)
    db.flush()
    for i in range(30):
        d = {"Matricula": f"ZZM{i}", "Nome": f"ZZTESTE SECRETO {i}", "Dt_Nasc": "1980-01-02", "Mae": "ZZTESTE MAE OCULTA",
             "Cpf": "00000000191" if i % 2 else "12345678900", "Cartao_Sus": None, "Sexo": "F",
             "Complemento": "RUA ZZTESTE PRIVADA", "Nr": "1", "Cep": None}
        db.add(RegistroBronze(sistema_origem="SAUDE", tabela="pacientes", id_origem=d["Matricula"], dados=d,
                              hash=f"zzm{i}", execucao_id=ex.id))
    db.flush()
    med = processamento.Medicao()
    try:
        with caplog.at_level(logging.INFO, logger="app.silver.medicao"):
            processamento.reconstruir(db, medicao=med)
        nomes = [e["etapa"] for e in med.etapas]
        for etapa in ("limpa_prata", "le_bronze_canonico", "passe_cpf", "passe_atributos", "grava_cidadaos",
                      "grava_vinculos", "grava_enderecos"):
            assert etapa in nomes
        for e in med.etapas:
            assert set(e) == {"etapa", "segundos", "linhas_entrada", "linhas_saida", "rss_mb", "rss_pico_mb"}
            assert e["segundos"] >= 0 and e["rss_pico_mb"] > 0
        texto = caplog.text
        assert "prata etapa=passe_cpf" in texto
        for proibido in ("SECRETO", "OCULTA", "PRIVADA", "00000000191", "12345678900", "ZZM1", "1980"):
            assert proibido not in texto
    finally:
        db.rollback()
