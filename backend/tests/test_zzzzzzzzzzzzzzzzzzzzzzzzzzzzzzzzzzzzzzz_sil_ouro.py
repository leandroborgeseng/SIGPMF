"""SIL no ouro (SO1-SO7): presença do cidadão PF correlacionado, sem campos da empresa. Só dados sintéticos ZZTESTE."""
from sqlalchemy import func, select

from app.gold import visao
from app.models.camadas import Cidadao, CidadaoSistemas, DadoGold, EmpresaSil

CID_A, CID_B = "zzteste-sil-ouro-a", "zzteste-sil-ouro-b"


def _monta(db):
    db.add_all([Cidadao(cidadao_id=CID_A, nome="ZZTESTE SIL A"), Cidadao(cidadao_id=CID_B, nome="ZZTESTE SIL B")])
    db.flush()
    db.add_all([
        EmpresaSil(ccm=990001, documento="00000000191", tipo_pessoa="PF", cidadao_id=CID_A, nome="ZZTESTE EMPRESA A1"),
        EmpresaSil(ccm=990002, documento="00000000191", tipo_pessoa="PF", cidadao_id=CID_A, nome="ZZTESTE EMPRESA A2"),
        EmpresaSil(ccm=990003, documento="00000000000191", tipo_pessoa="PJ", cidadao_id=None, nome="ZZTESTE PJ"),
    ])
    db.flush()


def _sil(db):
    return list(db.scalars(select(DadoGold).where(DadoGold.sistema == "SIL").order_by(DadoGold.cidadao_id)))


def test_so1_so3_presenca_sil_sem_campos_da_empresa(db):
    _monta(db)
    n_cid = db.scalar(select(func.count()).select_from(Cidadao))
    visao.reconstruir(db)
    linhas = _sil(db)
    assert [(d.cidadao_id, d.campo, d.valor) for d in linhas] == [(CID_A, "cadastro", "Empresas:990002")]
    assert not any("ZZTESTE EMPRESA" in (d.valor or "") or "191" in (d.valor or "") for d in linhas)
    a, b = db.get(CidadaoSistemas, CID_A), db.get(CidadaoSistemas, CID_B)
    assert "SIL" in a.sistemas and "SIL" not in b.sistemas
    # SO4: uma linha por cidadão; a SIL não cria cidadão
    assert db.scalar(select(func.count()).select_from(CidadaoSistemas)) == n_cid
    assert db.scalar(select(func.count()).select_from(Cidadao)) == n_cid


def test_so2_pj_sem_cidadao_fora_do_ouro(db):
    _monta(db)
    visao.reconstruir(db)
    assert all(d.cidadao_id != None for d in _sil(db))  # noqa: E711
    assert db.scalar(select(func.count()).select_from(DadoGold).where(DadoGold.valor == "Empresas:990003")) == 0


def test_so5_idempotente(db):
    _monta(db)
    visao.reconstruir(db)
    um = [(d.cidadao_id, d.campo, d.valor) for d in _sil(db)]
    tot1 = db.scalar(select(func.count()).select_from(DadoGold))
    visao.reconstruir(db)
    assert [(d.cidadao_id, d.campo, d.valor) for d in _sil(db)] == um
    assert db.scalar(select(func.count()).select_from(DadoGold)) == tot1


def test_m1_dominio_tributario_sem_sil_no_catalogo(db):
    from app.models.camadas import SistemaSilver
    db.query(SistemaSilver).filter(SistemaSilver.codigo == "SIL").delete()
    _monta(db)
    visao.reconstruir(db)
    a = db.get(CidadaoSistemas, CID_A)
    assert a.sistemas == ["SIL"] and a.dominios == ["tributario"]
    assert [d.dominio for d in _sil(db)] == ["tributario"]


def test_b3_so_sil_e_sem_sil(db):
    _monta(db)
    visao.reconstruir(db)
    assert db.get(CidadaoSistemas, CID_A).sistemas == ["SIL"]
    assert db.get(CidadaoSistemas, CID_B).sistemas == []


def test_b3_outro_sistema_mais_sil(db):
    from app.models.camadas import Vinculo
    _monta(db)
    db.add(Vinculo(cidadao_id=CID_A, sistema="SAUDE", tabela="zzteste", chave_origem="zzteste-1", bronze_id=-1,
                   metodo="cpf", confianca=1.0))
    db.flush()
    visao.reconstruir(db)
    a = db.get(CidadaoSistemas, CID_A)
    assert a.sistemas == ["SAUDE", "SIL"] and "tributario" in a.dominios
