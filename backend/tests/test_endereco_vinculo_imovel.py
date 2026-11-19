"""Endereço do imóvel na ficha: posse atual e anterior, com e sem SIL. Fixtures fictícias.

O número da casa (NR_CASE) continua no JSON; a tela só o esconde quando o endereço da SIL já traz o número.
"""
from datetime import date, datetime

from sqlalchemy import delete, event, text

from app.core.db import get_engine
from app.models.camadas import Cidadao, Imovel, ImovelPossuidor
from app.silver.endereco_imovel import (
    SQL_ENDERECOS_LOTE,
    chaves_inscricao,
    enderecos_por_inscricoes,
    rotulo_da_inscricao,
)

CID = "zzteste-vinc-end"
DOC = "000.000.000-00"
NOME = "ZZTESTE RESPONSAVEL"
ATUAL = "05555560066901"          # 14 dígitos; a SIL guarda 13, sem o zero à esquerda
ATUAL_CURTA = "5.5.55.56.006.69.01"
ATUAL_ROTULO = "Rua Exemplo de Toledo, 266 - Vila Exemplo"
ATUAL_ENDERECO = "14405-230 - RUA EXEMPLO DE TOLEDO, 00266  P/L.424 AREA B"
SEM_ATUAL = "06666670077012"
ANTERIOR = "07777780088123"
ANTERIOR_FMT = "07.7.77.78.008.81.23"
ANTERIOR_ROTULO = "Rua das Flores Ficticias, 42 - Jardim Exemplo"
ANTERIOR_ENDERECO = "14400-100 - RUA DAS FLORES FICTICIAS, 00042  CASA"
SEM_ANTERIOR = "08888890099234"
IDS = (880201, 880202, 880203, 880204)
CODIS = (ATUAL, SEM_ATUAL, ANTERIOR, SEM_ANTERIOR)


def test_chaves_completam_inscricao_com_zeros():
    assert chaves_inscricao(ATUAL_CURTA) == ["5555560066901", ATUAL]
    assert chaves_inscricao(ATUAL) == [ATUAL, "5555560066901"]
    assert chaves_inscricao(ANTERIOR_FMT) == [ANTERIOR, "7777780088123"]
    assert chaves_inscricao(None) == []


def test_lote_vazio_nao_consulta_e_um_lote_e_uma_consulta():
    class Resultado:
        def __iter__(self):
            return iter(())

    class Banco:
        def __init__(self):
            self.n = 0
            self.params = None
            self.sql = ""

        def execute(self, sql, params=None):
            self.n += 1
            self.sql = str(sql)
            self.params = params
            return Resultado()

    db = Banco()
    assert enderecos_por_inscricoes(db, []) == {}
    assert enderecos_por_inscricoes(db, [None, "   "]) == {}
    assert db.n == 0
    assert enderecos_por_inscricoes(db, [ATUAL_CURTA, SEM_ATUAL, ANTERIOR_FMT]) == {}
    assert db.n == 1
    baixo = db.sql.lower()
    assert "sil_imoveis_fisico" in baixo and "any" in baixo
    assert "cpfcnpj" not in baixo and "nomeresponsavel" not in baixo and "entrega" not in baixo
    esperado = set(chaves_inscricao(ATUAL_CURTA) + chaves_inscricao(SEM_ATUAL) + chaves_inscricao(ANTERIOR_FMT))
    assert set(db.params["digitos"]) == esperado
    assert "cpfcnpj" not in SQL_ENDERECOS_LOTE.lower()


def test_rotulo_casa_forma_curta_e_forma_com_zeros():
    mapa = {"5555560066901": ATUAL_ROTULO, ATUAL: ATUAL_ROTULO}
    assert rotulo_da_inscricao(mapa, ATUAL_CURTA) == ATUAL_ROTULO
    assert rotulo_da_inscricao(mapa, ATUAL) == ATUAL_ROTULO
    assert rotulo_da_inscricao({}, SEM_ATUAL) is None


def _limpa(db):
    db.execute(delete(ImovelPossuidor).where(ImovelPossuidor.cidadao_id == CID))
    db.execute(delete(Imovel).where(Imovel.codi.in_(CODIS)))
    db.execute(text("DELETE FROM bronze.sil_imoveis_fisico WHERE idfisico = ANY(:ids)"), {"ids": list(IDS)})
    db.execute(delete(Cidadao).where(Cidadao.cidadao_id == CID))
    db.commit()


def _prepara(db):
    _limpa(db)
    db.add(Cidadao(cidadao_id=CID, nome="Pessoa Ficticia", data_nascimento=date(1980, 1, 1)))
    db.add_all([
        Imovel(codi=ATUAL, inscricao=ATUAL, numero="00901"),
        Imovel(codi=SEM_ATUAL, inscricao=SEM_ATUAL, numero="00802", complemento="Fundos"),
        Imovel(codi=ANTERIOR, inscricao=ANTERIOR_FMT, numero="00703"),
        Imovel(codi=SEM_ANTERIOR, inscricao=SEM_ANTERIOR, numero="00604"),
    ])
    db.add_all([
        ImovelPossuidor(codi=ATUAL, documento="00000000000", tipo_pessoa="PF", cidadao_id=CID,
                        desde=datetime(2024, 6, 26), ate=None, ativo=True),
        ImovelPossuidor(codi=SEM_ATUAL, documento="00000000000", tipo_pessoa="PF", cidadao_id=CID,
                        desde=datetime(2023, 1, 15), ate=None, ativo=True),
        ImovelPossuidor(codi=ANTERIOR, documento="00000000000", tipo_pessoa="PF", cidadao_id=CID,
                        desde=datetime(2018, 1, 1), ate=datetime(2021, 8, 25), ativo=False),
        ImovelPossuidor(codi=SEM_ANTERIOR, documento="00000000000", tipo_pessoa="PF", cidadao_id=CID,
                        desde=datetime(2010, 5, 2), ate=datetime(2019, 3, 1), ativo=False),
    ])
    db.execute(text("""
        INSERT INTO bronze.sil_imoveis_fisico (
            idfisico, inscrfisico, imoveltplogradouro, imovellogradouro, imovelendereco, imovelbairro,
            cpfcnpjresponsavel, nomeresponsavel, atualizado_em)
        VALUES
        (880201, :curta, 'RUA', 'VELHA', '14400-000 - RUA VELHA, 00001', 'CENTRO', :doc, :nome, '2020-01-01'),
        (880202, :curta, 'RUA', 'EXEMPLO DE TOLEDO', :end_atual, 'VILA EXEMPLO', :doc, :nome, '2024-06-01'),
        (880203, :fmt, 'RUA', 'DAS FLORES FICTICIAS', :end_ant, 'JARDIM EXEMPLO', :doc, :nome, '2024-06-01'),
        (880204, '99.9.99.99.999.99.99', 'RUA', 'SEM POSSE', '14400-000 - RUA SEM POSSE, 00002', 'CENTRO', :doc, :nome, '2024-06-01')
    """), {"curta": ATUAL_CURTA, "fmt": ANTERIOR_FMT, "end_atual": ATUAL_ENDERECO, "end_ant": ANTERIOR_ENDERECO,
          "doc": DOC, "nome": NOME})
    db.commit()


def test_ficha_resolve_endereco_em_lote_na_posse_atual_e_na_anterior(client, tokens, db):
    _prepara(db)
    consultas = []

    def ouvir(_conn, _cur, st, *_a, **_k):
        if "sil_imoveis_fisico" in str(st).lower():
            consultas.append(str(st))

    event.listen(get_engine(), "before_cursor_execute", ouvir)
    try:
        r = client.get(f"/api/gold/cidadaos/{CID}/tributario", headers=tokens["leitura"])
    finally:
        event.remove(get_engine(), "before_cursor_execute", ouvir)
    try:
        assert r.status_code == 200, r.text
        assert len(consultas) == 1
        assert "any" in consultas[0].lower()
        por = {i["codi"]: i for i in r.json()["imoveis"]}
        assert set(por) == set(CODIS)
        assert por[ATUAL]["ativo"] is True and por[ATUAL]["endereco"] == ATUAL_ROTULO
        assert "VELHA" not in por[ATUAL]["endereco"] and por[ATUAL]["numero"] == "00901"
        assert por[SEM_ATUAL]["ativo"] is True and por[SEM_ATUAL]["endereco"] is None
        assert por[ANTERIOR]["ativo"] is False and por[ANTERIOR]["endereco"] == ANTERIOR_ROTULO
        assert por[ANTERIOR]["ate"].startswith("2021-08-25")
        assert por[SEM_ANTERIOR]["ativo"] is False and por[SEM_ANTERIOR]["endereco"] is None
        assert por[SEM_ANTERIOR]["ate"].startswith("2019-03-01")
        assert NOME not in r.text and DOC not in r.text and "00000000000" not in r.text
        assert "99999999999999" not in r.text
    finally:
        _limpa(db)
