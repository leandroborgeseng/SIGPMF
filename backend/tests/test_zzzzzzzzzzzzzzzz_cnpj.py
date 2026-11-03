"""CN1 (CNPJ alfanumérico, Receita jul/2026) e CN4 (busca de empresa por CNPJ, raiz ou CCM).

CN1: 12 posições [0-9A-Z] + 2 DV numéricos; DV pelo valor ASCII − 48. O CNPJ numérico e o CPF não mudam.
CN4: GET /api/silver/empresas junta SIL e IPTU; documento e nome sempre mascarados (SIL16); não busca por nome;
     a auditoria guarda modo e quantidade, nunca o CNPJ buscado.
"""
from sqlalchemy import delete, select

from app.connectors.sil.prata import digitos
from app.explorador.mascara import _texto_livre
from app.identity import documento as docmod
from app.models.camadas import EmpresaSil, ImovelPossuidor
from app.models.controle import Auditoria

ALFA = "12ABC34501DE35"  # exemplo publicado pela Receita (12.ABC.345/01DE-35)
NUM = "11222333000181"


def test_cn1_valida_alfanumerico_e_mantem_numerico():
    assert docmod.cnpj_valido("12.ABC.345/01DE-35")
    assert docmod.cnpj_valido("12.abc.345/01de-35")  # minúscula vira maiúscula
    assert docmod.normaliza(" 12.abc.345/01de-35 ") == ALFA
    assert not docmod.cnpj_valido("12.ABC.345/01DE-36")  # DV errado
    assert docmod.cnpj_valido("11.222.333/0001-81") and docmod.normaliza("11.222.333/0001-81") == NUM
    assert docmod.normaliza("123.456.789-09") == "12345678909"  # CPF continua só dígitos
    assert docmod.normaliza("12.345.678-X") == "12345678"  # RG com letra não é CNPJ: comportamento antigo


def test_cn1_classifica_e_mascara():
    assert docmod.classifica("12.ABC.345/01DE-35") == docmod.Classificacao("cnpj", ALFA, None)
    assert docmod.classifica("12.ABC.345/01DE-99").motivo == docmod.MOTIVO_CNPJ_INVALIDO
    assert docmod.classifica("JOSE DA SILVA").motivo == docmod.MOTIVO_DOCUMENTO_AMBIGUO
    assert docmod.classifica(NUM) == docmod.Classificacao("cnpj", NUM, None)
    assert docmod.mascara(ALFA) == "**.ABC.***/****-**"
    assert ALFA not in _texto_livre(f"pagamento do CNPJ {ALFA} em dia") and "12.ABC.345/01DE-35" not in _texto_livre(
        "CNPJ 12.ABC.345/01DE-35")
    assert digitos("12.abc.345/01de-35") == ALFA and digitos("11.222.333/0001-81") == NUM


def test_cn4_busca_empresa(client, tokens, db):
    filial = "12ABC345" + "0002" + "00"  # mesma raiz, outro estabelecimento (DV não importa para a busca por raiz)
    try:
        db.add_all([EmpresaSil(ccm=990001, documento=ALFA, tipo_pessoa="PJ", nome="Padaria Exemplo Ltda", possui_debito=True),
                    EmpresaSil(ccm=990002, documento=filial, tipo_pessoa="PJ", nome="Padaria Exemplo Filial"),
                    EmpresaSil(ccm=990003, documento="12345678909", tipo_pessoa="PF", nome="Fulano MEI"),
                    ImovelPossuidor(codi="CN4-1", documento=ALFA, tipo_pessoa="PJ", ativo=True),
                    ImovelPossuidor(codi="CN4-2", documento=ALFA, tipo_pessoa="PJ", ativo=False)])
        db.commit()
        h = tokens["leitura"]
        r = client.get("/api/silver/empresas", params={"busca": "12.abc.345/01de-35"}, headers=h)
        assert r.status_code == 200, r.text
        (e,) = r.json()
        assert e["ccms"] == [990001] and e["possui_debito"] is True and e["imoveis_ativos"] == 1
        assert e["fontes"] == ["SIL", "IPTU"] and e["mascarado"] is True
        assert ALFA not in r.text and "Padaria Exemplo" not in r.text  # SIL16: documento e nome mascarados
        raiz = client.get("/api/silver/empresas", params={"busca": "12.ABC.345"}, headers=h).json()
        assert sorted(x["ccms"][0] for x in raiz) == [990001, 990002]
        por_ccm = client.get("/api/silver/empresas", params={"busca": "990003"}, headers=h).json()
        assert [(x["ccms"], x["tipo_pessoa"]) for x in por_ccm] == [([990003], "PF")]
        assert client.get("/api/silver/empresas", params={"busca": "12.ABC.345/01DE-36"}, headers=h).status_code == 400
        assert client.get("/api/silver/empresas", params={"busca": "123.456.789-09"}, headers=h).status_code == 400  # CPF
        assert client.get("/api/silver/empresas", params={"busca": "Padaria"}, headers=h).status_code == 400  # sem nome
        aud = db.scalars(select(Auditoria).where(Auditoria.acao == "busca_empresa").order_by(Auditoria.id.desc())).first()
        assert aud.detalhe == {"modo": "ccm", "resultados": 1} and ALFA not in str(aud.detalhe)
    finally:
        db.rollback()
        db.execute(delete(EmpresaSil).where(EmpresaSil.ccm.in_([990001, 990002, 990003])))
        db.execute(delete(ImovelPossuidor).where(ImovelPossuidor.codi.in_(["CN4-1", "CN4-2"])))
        db.commit()
