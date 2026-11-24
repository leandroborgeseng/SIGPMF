"""Carga do CSV fictício do cadastro físico da SIL: parse latin1, upsert e catálogo."""
import io
from pathlib import Path

import pytest
from sqlalchemy import text

from app.chat import schema_cat as sc
from app.chat.catalogo import entidades
from app.chat.schema_cat import _classe, invalidar
from app.services import carga_sil_imoveis as ic

FIXTURE = Path(__file__).parent / "fixtures" / "sil_imoveis_fisico_amostra.csv"
DOC = "000.000.000-00"
TIPO = ic.TIPO


def test_classe_colada_nao_muda_coluna_com_underscore():
    assert _classe("cpfcnpjresponsavel", None) == "oculta"
    assert _classe("nomeresponsavel", None) == "sensivel"
    assert _classe("imovelendereco", None) == "sensivel"
    assert _classe("imovellogradouro", None) == "sensivel"
    assert _classe("imovelcep", None) == "sensivel"
    assert _classe("entregabairro", None) == "sensivel"
    assert _classe("situacaofisico", None) == "normal"
    assert _classe("idfisico", None) == "normal"
    assert _classe("credor_cnpj", None) == "oculta"
    assert _classe("credor_nome", None) == "sensivel"
    assert _classe("zzmarcador", None) == "normal"
    assert _classe("cpf", None) == "oculta"


def test_parse_amostra_latin1_ignora_ponto_e_virgula_final():
    bruto = FIXTURE.read_bytes()
    texto = bruto.decode("latin-1")
    assert "CAPITÃO" in texto and "CICLANO ÁTILA" in texto
    with pytest.raises(UnicodeDecodeError):
        bruto.decode("utf-8")
    assert texto.splitlines()[0].endswith(";")
    with FIXTURE.open(encoding="latin-1", newline="") as arq:
        lidos, dups, regs = next(ic.iter_lotes(arq, tamanho=10))
    por_id = {r["idfisico"]: r for r in regs}
    assert lidos == 2 and dups == 0 and set(por_id) == {1442, 1443}
    assert set(por_id[1442]) == set(ic.COLUNAS)
    assert "CAPITÃO" in por_id[1442]["imovelendereco"]
    assert "  P/L.409" in por_id[1442]["imovelendereco"]
    assert por_id[1442]["cpfcnpjresponsavel"] == DOC
    assert por_id[1443]["nomeresponsavel"] == "CICLANO ÁTILA"
    assert por_id[1443]["cpfcnpjresponsavel"] == "00.000.000/0001-00"


def test_iter_lotes_recusa_cabecalho_e_deduplica_no_lote():
    ruim = io.StringIO("foo;bar;\n1;2;\n")
    with pytest.raises(ic.ArquivoInvalido):
        next(ic.iter_lotes(ruim))
    linha = ";".join("1" if c == "idfisico" else "A" for c in ic.COLUNAS) + ";"
    outra = ";".join("1" if c == "idfisico" else "B" for c in ic.COLUNAS) + ";"
    cab = ";".join(ic.COLUNAS) + ";\n"
    lidos, dups, regs = next(ic.iter_lotes(io.StringIO(cab + linha + "\n" + outra + "\n")))
    assert lidos == 2 and dups == 1 and len(regs) == 1 and regs[0]["situacaofisico"] == "B"


def _limpa(db):
    db.execute(text("DELETE FROM controle.logs WHERE execucao_id IN "
                    "(SELECT id FROM controle.execucoes WHERE tipo=:t)"), {"t": TIPO})
    db.execute(text("DELETE FROM controle.execucoes WHERE tipo=:t"), {"t": TIPO})
    db.execute(text("DELETE FROM bronze.sil_imoveis_fisico"))
    db.commit()
    invalidar()


def test_upsert_idempotente_execucao_e_catalogo(db, tmp_path):
    _limpa(db)
    try:
        n0 = db.scalar(text("SELECT count(*) FROM controle.execucoes WHERE tipo=:t"), {"t": TIPO})
        with pytest.raises(ic.ArquivoInvalido):
            ic.carregar(db, io.StringIO("errado;\n"), usuario="zzteste")
        assert db.scalar(text("SELECT count(*) FROM controle.execucoes WHERE tipo=:t"), {"t": TIPO}) == n0

        ex1 = ic.carregar(db, FIXTURE, usuario="zzteste", lote=1)
        assert ex1.sistema == "SIL" and ex1.tipo == TIPO and ex1.status == "sucesso"
        assert ex1.finalizado_em is not None
        assert (ex1.lidos, ex1.novos, ex1.atualizados, ex1.ignorados, ex1.lotes) == (2, 2, 0, 0, 2)
        assert DOC not in (ex1.mensagem or "")
        logs = db.scalars(text("SELECT mensagem FROM controle.logs WHERE execucao_id=:i"), {"i": ex1.id}).all()
        assert logs and all(DOC not in m for m in logs)
        assert db.scalar(text("SELECT count(*) FROM bronze.sil_imoveis_fisico")) == 2
        assert db.scalar(text("SELECT cpfcnpjresponsavel FROM bronze.sil_imoveis_fisico WHERE idfisico=1442")) == DOC
        marca = db.execute(text(
            "SELECT carregado_em, situacaofisico FROM bronze.sil_imoveis_fisico WHERE idfisico=1442")).one()

        ex2 = ic.carregar(db, FIXTURE, usuario="zzteste", lote=1)
        assert (ex2.lidos, ex2.novos, ex2.atualizados, ex2.ignorados) == (2, 0, 0, 2)
        assert ex2.sistema == "SIL" and ex2.finalizado_em is not None
        assert db.scalar(text("SELECT count(*) FROM bronze.sil_imoveis_fisico")) == 2
        de_novo = db.execute(text(
            "SELECT carregado_em, situacaofisico FROM bronze.sil_imoveis_fisico WHERE idfisico=1442")).one()
        assert de_novo.carregado_em == marca.carregado_em and de_novo.situacaofisico == "Ativo"

        alterado = tmp_path / "atualizado.csv"
        alterado.write_bytes(FIXTURE.read_bytes().replace(b"Ativo", b"Baixado", 1))
        ex3 = ic.carregar(db, alterado, usuario="zzteste", lote=1)
        assert (ex3.novos, ex3.atualizados, ex3.ignorados) == (0, 1, 1)
        assert db.scalar(text("SELECT count(*) FROM bronze.sil_imoveis_fisico")) == 2
        linha = db.execute(text(
            "SELECT situacaofisico, carregado_em, nomeresponsavel FROM bronze.sil_imoveis_fisico "
            "WHERE idfisico=1442")).one()
        assert linha.situacaofisico == "Baixado"
        assert linha.carregado_em == marca.carregado_em
        assert linha.nomeresponsavel == "FULANO DE TAL"
        assert db.scalar(text(
            "SELECT count(*) FROM controle.execucoes WHERE tipo=:t AND sistema='SIL' AND finalizado_em IS NOT NULL"),
            {"t": TIPO}) == 3

        fonte = Path(sc.__file__).read_text(encoding="utf-8")
        assert "sil_imoveis_fisico" not in fonte and "cpfcnpjresponsavel" not in fonte
        invalidar()
        ent = entidades(db)["bronze.sil_imoveis_fisico"]
        assert ent.fonte_id == "iptu" and not ent.somente_agregados
        assert "imóve" in ent.significado.lower() or "imove" in ent.significado.lower()
        assert "cpfcnpjresponsavel" not in ent.campos
        assert ent.campos["nomeresponsavel"].sensivel
        assert ent.campos["imovelendereco"].sensivel
        assert ent.campos["imovelcep"].sensivel
        assert ent.campos["entregabairro"].sensivel
        assert not ent.campos["situacaofisico"].sensivel
        cols = db.scalars(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema='bronze' AND table_name='sil_imoveis_fisico'")).all()
        assert "cpfcnpjresponsavel" in cols
    finally:
        _limpa(db)
