"""Rua e número no evento de posse. Fixtures fictícias; o número mora em imovelendereco."""
from sqlalchemy import text

from app.silver.endereco_imovel import SQL_ENDERECOS, mapa_enderecos, rotulo_endereco, so_digitos, titulo_posse

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
