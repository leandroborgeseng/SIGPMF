"""NOMES da carga completa tem de cobrir cada conector de CONECTORES_COMPLETA.

Sem a chave, a mensagem de _finaliza estoura KeyError (sucesso na carga concluída e falha no
except de rodar). O rollback desfaz a transação aberta e a execução fica em em_andamento.
A origem não é aberta e prata/ouro não são reconstruídas: o teste só grava a execução no
Postgres de teste.
"""
import pytest

from app.core.db import SessionLocal
from app.models.controle import Execucao
from app.services import carga_completa as ccomp
from app.services import trava


class _OrigemFechada:
    """Leitor que prova que a finalização não abre a origem."""

    def __init__(self):
        self.aberta = False

    def abrir(self):
        self.aberta = True
        raise AssertionError("a origem não deve ser aberta ao finalizar")


def _execucao(db, conector: str) -> Execucao:
    ex = Execucao(tipo=ccomp.TIPO, sistema=ccomp._sistema(conector), status="em_andamento",
                  usuario="zzteste@aion.local", modo="completa", lidos=0, novos=0, atualizados=0, ignorados=0)
    db.add(ex)
    db.commit()
    db.refresh(ex)
    return ex


def _gravada(ex_id: int) -> Execucao:
    """Lê o que foi confirmado no banco, não o objeto ainda pendente na sessão do teste."""
    outra = SessionLocal()
    try:
        ex = outra.get(Execucao, ex_id)
        outra.expunge(ex)
        return ex
    finally:
        outra.close()


def _prepara(monkeypatch, desfecho: str) -> None:
    if desfecho == "falha":
        def _quebra(*_a, **_k):
            raise RuntimeError("zz-falha-sintetica")
        monkeypatch.setattr(ccomp, "_executa", _quebra)
        return

    monkeypatch.setattr("app.services.camadas_estado.inalterada", lambda _db, _assin: True)

    def _camadas_sem_reconstruir(db, ex, conector, sistema, _leitor):
        ccomp._camadas(db, ex, conector, sistema, pode_pular=True)
    monkeypatch.setattr(ccomp, "_executa", _camadas_sem_reconstruir)


def _finaliza(db, monkeypatch, conector: str, desfecho: str) -> Execucao:
    _prepara(monkeypatch, desfecho)
    ex = _execucao(db, conector)
    origem = _OrigemFechada()
    ccomp.rodar(ex.id, conector, None, leitor=origem, db=db)
    assert origem.aberta is False
    return _gravada(ex.id)


def test_todo_conector_da_completa_tem_chave_em_nomes():
    faltando = sorted(set(ccomp.CONECTORES_COMPLETA) - set(ccomp.NOMES))
    assert faltando == []
    assert all(isinstance(ccomp.NOMES[c], str) and ccomp.NOMES[c].strip() for c in ccomp.CONECTORES_COMPLETA)
    assert ccomp.NOMES["senior"] == "Senior"
    assert ccomp.NOMES["senior"] == trava.NOMES["senior"]


@pytest.mark.parametrize("conector", ccomp.CONECTORES_COMPLETA)
@pytest.mark.parametrize("desfecho", ("sucesso", "falha"))
def test_finalizar_sucesso_e_falha_sai_de_em_andamento(db, monkeypatch, conector, desfecho):
    gravada = _finaliza(db, monkeypatch, conector, desfecho)
    nome = ccomp.NOMES[conector]
    assert gravada.status == desfecho
    assert gravada.status != "em_andamento"
    assert gravada.finalizado_em is not None
    if desfecho == "sucesso":
        assert gravada.mensagem.startswith(f"Carga completa de {nome} concluída")
    else:
        assert gravada.mensagem.startswith(f"Falha na carga completa de {nome}:")


@pytest.mark.parametrize("desfecho", ("sucesso", "falha"))
def test_nome_ausente_usa_o_id_do_conector(db, monkeypatch, desfecho):
    nomes = {k: v for k, v in ccomp.NOMES.items() if k != "senior"}
    monkeypatch.setattr(ccomp, "NOMES", nomes)
    gravada = _finaliza(db, monkeypatch, "senior", desfecho)
    assert gravada.status == desfecho and gravada.status != "em_andamento"
    assert gravada.finalizado_em is not None
    if desfecho == "sucesso":
        assert gravada.mensagem.startswith("Carga completa de senior concluída")
    else:
        assert gravada.mensagem.startswith("Falha na carga completa de senior:")
    assert "Senior" not in gravada.mensagem
