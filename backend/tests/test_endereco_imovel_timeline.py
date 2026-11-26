"""Rua e número no evento de posse. Fixtures fictícias; o número mora em imovelendereco."""
from datetime import date, datetime

from sqlalchemy import event, text

from app.core.db import get_engine
from app.models.camadas import Imovel, ImovelPossuidor
from app.silver.endereco_imovel import SQL_ENDERECOS, mapa_enderecos, rotulo_endereco, so_digitos, titulo_posse
from app.silver.fatos import _eventos

INSCR = "01111120022505"
FORMATADA = "01.1.11.12.002.25.05"
ENDERECO = "14405-230 - RUA EXEMPLO DE TOLEDO, 00266  P/L.424 AREA B"
ROTULO = "Rua Exemplo de Toledo, 266 - Vila Exemplo"
DOC = "000.000.000-00"
NOME = "FULANO DE TAL"


def test_so_digitos_casa_inscricao_formatada():
    assert so_digitos(FORMATADA) == INSCR
    assert len(so_digitos(FORMATADA)) == 14


def test_com_endereco_numero_zeros_e_complemento():
    rotulo = rotulo_endereco("RUA", "EXEMPLO DE TOLEDO", ENDERECO, "VILA EXEMPLO")
    assert rotulo == ROTULO
    assert "14405" not in rotulo and "00266" not in rotulo
    assert "P/L" not in rotulo and "424" not in rotulo and "AREA" not in rotulo
    assert DOC not in rotulo and NOME not in rotulo
    assert titulo_posse(INSCR, rotulo) == f"Passou a constar como possuidor do imóvel {INSCR} ({ROTULO})"


def test_numero_zerado_omite():
    rotulo = rotulo_endereco("RUA", "EXEMPLO DE TOLEDO", "14405-230 - RUA EXEMPLO DE TOLEDO, 00000  P/L.1", "VILA EXEMPLO")
    assert rotulo == "Rua Exemplo de Toledo - Vila Exemplo"


def test_sem_endereco_mantem_titulo():
    assert rotulo_endereco(None, None, None, None) is None
    assert rotulo_endereco("  ", "", "   ", "VILA EXEMPLO") is None
    assert titulo_posse(INSCR, None) == f"Passou a constar como possuidor do imóvel {INSCR}"


def test_parse_falho_usa_tipo_logradouro_bairro_sem_numero():
    rotulo = rotulo_endereco("RUA", "URIAS BATISTA DE AVELAR", "texto sem formato", "JARDIM EXEMPLO")
    assert rotulo == "Rua Urias Batista de Avelar - Jardim Exemplo"
    assert "texto" not in rotulo
    curto = rotulo_endereco("AVENIDA", "FICTICIA", "14400-000 - AVENIDA FICTÍCIA, 10", "CENTRO")
    assert curto == "Avenida Ficticia - Centro"


def test_sql_nao_le_documento_nem_nome_e_uma_consulta():
    baixo = SQL_ENDERECOS.lower()
    assert "cpfcnpj" not in baixo and "nomeresponsavel" not in baixo and "entrega" not in baixo

    class Resultado:
        def __iter__(self):
            return iter(())

    class Banco:
        def __init__(self):
            self.n = 0

        def execute(self, *_a, **_k):
            self.n += 1
            return Resultado()

    db = Banco()
    assert mapa_enderecos(db) == {}
    assert db.n == 1


def test_join_em_lote_prefere_o_mais_recente_e_nao_expoe_responsavel(db):
    db.execute(text("DELETE FROM silver.imovel_possuidores WHERE codi = :c"), {"c": INSCR})
    db.execute(text("DELETE FROM bronze.sil_imoveis_fisico WHERE idfisico IN (880001, 880002, 880003)"))
    db.execute(text("""
        INSERT INTO silver.imovel_possuidores (codi, documento, tipo_pessoa, cidadao_id, desde, ativo)
        VALUES (:c, '00000000000', 'PF', 'zzteste-end', '2019-01-01', true)
    """), {"c": INSCR})
    db.execute(text("""
        INSERT INTO bronze.sil_imoveis_fisico (
            idfisico, inscrfisico, imoveltplogradouro, imovellogradouro, imovelendereco, imovelbairro,
            cpfcnpjresponsavel, nomeresponsavel, atualizado_em)
        VALUES
        (880001, :fmt, 'RUA', 'VELHA', '14400-000 - RUA VELHA, 00001', 'CENTRO', :doc, :nome, '2020-01-01'),
        (880002, :fmt, 'RUA', 'EXEMPLO DE TOLEDO', :end, 'VILA EXEMPLO', :doc, :nome, '2024-06-01'),
        (880003, '99.9.99.99.999.99.99', 'RUA', 'SEM POSSE', '14400-000 - RUA SEM POSSE, 00002', 'CENTRO', :doc, :nome, '2024-06-01')
    """), {"fmt": FORMATADA, "end": ENDERECO, "doc": DOC, "nome": NOME})
    db.flush()
    try:
        assert db.scalar(text("SELECT bronze.inscricao_so_digitos(:f)"), {"f": FORMATADA}) == INSCR
        mapa = mapa_enderecos(db)
        assert mapa[INSCR] == ROTULO
        assert "99999999999999" not in mapa
        texto = titulo_posse(INSCR, mapa[INSCR])
        assert DOC not in texto and NOME not in texto and "VELHA" not in texto
        assert db.scalar(text(
            "SELECT 1 FROM pg_indexes WHERE schemaname = 'bronze' AND indexname = 'ix_sil_imoveis_fisico_inscr_digitos'"
        )) == 1
    finally:
        db.rollback()


# Inscrições e ruas fictícias. Nenhum CPF real: documento zerado e nome de teste.
_CID = "zzteste-tl-end"
_DOC = "000.000.000-00"
_DOC_DIG = "00000000000"
_NOME = "PESSOA FICTICIA"
_CODI_EXATO = "04444450055890"
_FMT_EXATO = "04.4.44.45.005.58.90"
_END_EXATO = "14401-000 - RUA NOVA FICTICIA, 00100  CASA"
_ROT_EXATO = "Rua Nova Ficticia, 100 - Vila Teste"
_CODI_14 = "05555560066901"
_SIL_13 = "5.5.55.56.006.69.01"
_END_13 = "14405-230 - RUA EXEMPLO DE TOLEDO, 00266  P/L.424 AREA B"
_ROT_13 = "Rua Exemplo de Toledo, 266 - Vila Exemplo"
_CODI_12 = "123456789012"
_SIL_12 = "00.12.34.56.789.01.2"
_END_12 = "14400-100 - RUA DAS FLORES FICTICIAS, 00042  CASA"
_ROT_12 = "Rua das Flores Ficticias, 42 - Jardim Exemplo"
_CODI_13 = "2345678901234"
_SIL_14 = "02.3.45.67.890.12.34"
_END_14 = "14402-000 - RUA CURTA FICTICIA, 00007  CASA"
_ROT_14 = "Rua Curta Ficticia, 7 - Bairro Exemplo"
_CODI_SEM = "06666670077012"
_CODI_INSC = "07777780088123"
_FMT_INSC = "08.8.88.89.009.92.34"
_SIL_INSC = "8.8.88.89.009.92.34"
_END_INSC = "14400-000 - AVENIDA FICTICIA, 00010  CASA"
_ROT_INSC = "Avenida Ficticia, 10 - Centro Exemplo"
_CODI_ORFAO = "03333340044789"
_IDS_EVT = (880411, 880412, 880413, 880414, 880415, 880416, 880417)
_CODIS_EVT = (_CODI_EXATO, _CODI_14, _CODI_12, _CODI_13, _CODI_SEM, _CODI_INSC, _CODI_ORFAO, "02222230033678", "02111120022567")


def test_evento_posse_titulo_com_e_sem_endereco_e_pad_12_13_14(db):
    """O texto do evento não muda. O lote casa CODI e inscrição no pad 12/13↔14, numa consulta."""
    assert so_digitos(_FMT_EXATO) == _CODI_EXATO
    assert so_digitos(_SIL_13) == "5555560066901" and _CODI_14.endswith(so_digitos(_SIL_13))
    assert so_digitos(_SIL_12) == _CODI_12.zfill(14)
    assert so_digitos(_SIL_14) == _CODI_13.zfill(14)
    assert so_digitos(_FMT_INSC) == "08888890099234" and so_digitos(_SIL_INSC) == "8888890099234"
    assert rotulo_endereco("RUA", "NOVA FICTICIA", _END_EXATO, "VILA TESTE") == _ROT_EXATO
    assert rotulo_endereco("RUA", "EXEMPLO DE TOLEDO", _END_13, "VILA EXEMPLO") == _ROT_13
    assert rotulo_endereco("RUA", "DAS FLORES FICTICIAS", _END_12, "JARDIM EXEMPLO") == _ROT_12
    assert rotulo_endereco("RUA", "CURTA FICTICIA", _END_14, "BAIRRO EXEMPLO") == _ROT_14
    assert rotulo_endereco("AVENIDA", "FICTICIA", _END_INSC, "CENTRO EXEMPLO") == _ROT_INSC

    db.execute(text("DELETE FROM silver.imovel_possuidores WHERE cidadao_id = :c OR codi = ANY(:codis)"),
               {"c": _CID, "codis": list(_CODIS_EVT)})
    db.execute(text("DELETE FROM silver.imoveis WHERE codi = ANY(:codis)"), {"codis": list(_CODIS_EVT)})
    db.execute(text("DELETE FROM bronze.sil_imoveis_fisico WHERE idfisico = ANY(:ids)"), {"ids": list(_IDS_EVT)})
    db.add_all([
        Imovel(codi=_CODI_EXATO, inscricao=_FMT_EXATO),
        Imovel(codi=_CODI_14, inscricao=_CODI_14),
        Imovel(codi=_CODI_12, inscricao=_CODI_12),
        Imovel(codi=_CODI_13, inscricao=_CODI_13),
        Imovel(codi=_CODI_SEM, inscricao="06.6.66.67.007.70.12"),
        Imovel(codi=_CODI_INSC, inscricao=_FMT_INSC),
    ])
    db.add_all([
        ImovelPossuidor(codi=_CODI_EXATO, documento=_DOC_DIG, tipo_pessoa="PF", cidadao_id=_CID,
                        desde=datetime(2019, 3, 15), ativo=True),
        ImovelPossuidor(codi=_CODI_14, documento=_DOC_DIG, tipo_pessoa="PF", cidadao_id=_CID,
                        desde=datetime(2020, 7, 1), ativo=True),
        ImovelPossuidor(codi=_CODI_12, documento=_DOC_DIG, tipo_pessoa="PF", cidadao_id=_CID,
                        desde=datetime(2021, 11, 20), ativo=True),
        ImovelPossuidor(codi=_CODI_13, documento=_DOC_DIG, tipo_pessoa="PF", cidadao_id=_CID,
                        desde=datetime(2022, 1, 9), ativo=True),
        ImovelPossuidor(codi=_CODI_SEM, documento=_DOC_DIG, tipo_pessoa="PF", cidadao_id=_CID,
                        desde=datetime(2018, 2, 2), ativo=True),
        ImovelPossuidor(codi=_CODI_INSC, documento=_DOC_DIG, tipo_pessoa="PF", cidadao_id=_CID,
                        desde=datetime(2022, 4, 4), ativo=False),
        ImovelPossuidor(codi=_CODI_ORFAO, documento=_DOC_DIG, tipo_pessoa="PF", cidadao_id=_CID,
                        desde=datetime(2017, 5, 5), ativo=True),
        ImovelPossuidor(codi="02222230033678", documento=_DOC_DIG, tipo_pessoa="PF", cidadao_id=None,
                        desde=datetime(2016, 1, 1), ativo=True),
        ImovelPossuidor(codi="02111120022567", documento=_DOC_DIG, tipo_pessoa="PF", cidadao_id=_CID,
                        desde=None, ativo=True),
    ])
    db.flush()
    db.execute(text("""
        INSERT INTO bronze.sil_imoveis_fisico (
            idfisico, inscrfisico, imoveltplogradouro, imovellogradouro, imovelendereco, imovelbairro,
            cpfcnpjresponsavel, nomeresponsavel, atualizado_em)
        VALUES
        (880411, :fmt_exato, 'RUA', 'NOVA FICTICIA', :end_exato, 'VILA TESTE', :doc, :nome, '2024-06-01'),
        (880417, :sil_13, 'RUA', 'VELHA', '14400-000 - RUA VELHA, 00001', 'CENTRO', :doc, :nome, '2020-01-01'),
        (880412, :sil_13, 'RUA', 'EXEMPLO DE TOLEDO', :end_13, 'VILA EXEMPLO', :doc, :nome, '2024-06-01'),
        (880413, :sil_12, 'RUA', 'DAS FLORES FICTICIAS', :end_12, 'JARDIM EXEMPLO', :doc, :nome, '2024-06-01'),
        (880414, :sil_14, 'RUA', 'CURTA FICTICIA', :end_14, 'BAIRRO EXEMPLO', :doc, :nome, '2024-06-01'),
        (880415, :sil_insc, 'AVENIDA', 'FICTICIA', :end_insc, 'CENTRO EXEMPLO', :doc, :nome, '2024-06-01'),
        (880416, '99.9.99.99.999.99.99', 'RUA', 'SEM POSSE', '14400-000 - RUA SEM POSSE, 00002', 'CENTRO', :doc, :nome, '2024-06-01')
    """), {"fmt_exato": _FMT_EXATO, "sil_13": _SIL_13, "sil_12": _SIL_12, "sil_14": _SIL_14, "sil_insc": _SIL_INSC,
          "end_exato": _END_EXATO, "end_13": _END_13, "end_12": _END_12, "end_14": _END_14, "end_insc": _END_INSC,
          "doc": _DOC, "nome": _NOME})
    db.flush()

    consultas = []

    def ouvir(_conn, _cur, st, *_a, **_k):
        if "sil_imoveis_fisico" in str(st).lower():
            consultas.append(str(st))

    event.listen(get_engine(), "before_cursor_execute", ouvir)
    try:
        eventos = [e for e in _eventos(db, {}, {"especialidades": {}}) if e.cidadao_id == _CID]
    finally:
        event.remove(get_engine(), "before_cursor_execute", ouvir)
    try:
        assert len(consultas) == 1, consultas
        assert "any" in consultas[0].lower()
        assert len(eventos) == 7
        por = {}
        for ev in eventos:
            assert ev.area == "impostos" and ev.tipo == "posse" and ev.fonte == "IPTU"
            achou = [c for c in _CODIS_EVT if f"imóvel {c}" in ev.titulo]
            assert len(achou) == 1, ev.titulo
            por[achou[0]] = ev
        assert set(por) == {_CODI_EXATO, _CODI_14, _CODI_12, _CODI_13, _CODI_SEM, _CODI_INSC, _CODI_ORFAO}

        def confere(codi, rotulo, dia):
            titulo = por[codi].titulo
            assert titulo == titulo_posse(codi, rotulo)
            if rotulo:
                assert titulo == f"Passou a constar como possuidor do imóvel {codi} ({rotulo})"
            else:
                assert titulo == f"Passou a constar como possuidor do imóvel {codi}"
                assert "(" not in titulo
            assert por[codi].data == dia
            assert "não informado" not in titulo and "n/d" not in titulo.casefold()

        confere(_CODI_EXATO, _ROT_EXATO, date(2019, 3, 15))
        confere(_CODI_14, _ROT_13, date(2020, 7, 1))
        confere(_CODI_12, _ROT_12, date(2021, 11, 20))
        confere(_CODI_13, _ROT_14, date(2022, 1, 9))
        confere(_CODI_SEM, None, date(2018, 2, 2))
        confere(_CODI_INSC, _ROT_INSC, date(2022, 4, 4))
        confere(_CODI_ORFAO, None, date(2017, 5, 5))
        texto = " ".join(e.titulo for e in eventos)
        assert _DOC not in texto and _DOC_DIG not in texto and _NOME not in texto
        assert "VELHA" not in texto and "SEM POSSE" not in texto
        assert "02222230033678" not in texto and "02111120022567" not in texto
    finally:
        db.rollback()
