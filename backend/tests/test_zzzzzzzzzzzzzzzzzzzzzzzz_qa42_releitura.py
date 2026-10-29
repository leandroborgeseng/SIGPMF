"""QA #42 (Tester): a releitura de uma fonte da educação é recusada com carga rodando, sem mexer nos checkpoints.
Só ZZTESTE; origem nunca é tocada."""
import pytest
from sqlalchemy import select

from app.models.controle import CheckpointCarga, Execucao
from app.services import carga_gestaoescolar as cge
from tests.test_zzzzzzzzzzzzzzzzzzz_gestaoescolar import janela_env as janela_env  # noqa: F401
from tests.test_zzzzzzzzzzzzzzzzzzz_gestaoescolar import limpo as limpo  # noqa: F401

SIS = ("GESTAOESCOLAR", "CORESSO")


def _cks(db):
    return {c.tabela: (c.posicao, c.ultima_chave, c.concluida) for c in
            db.scalars(select(CheckpointCarga).where(CheckpointCarga.sistema.in_(SIS)))}


def test_qa42_releitura_recusa_com_carga_rodando(janela_env, limpo):
    db = limpo
    db.add(Execucao(tipo=cge.TIPO_COMPLETA, sistema="GESTAOESCOLAR", status="em_andamento", usuario="ZZTESTE"))
    db.commit()
    antes = _cks(db)
    try:
        with pytest.raises(cge.CargaRecusada):
            cge.prepara_releitura(db, "PES_PessoaDocumento", "zzteste@aion.local")
        assert _cks(db) == antes
    finally:
        db.query(Execucao).filter(Execucao.tipo == cge.TIPO_COMPLETA, Execucao.status == "em_andamento").delete()
        db.commit()
