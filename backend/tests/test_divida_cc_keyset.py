"""Keyset da Divida_Conta_Corrente alinhado à PK clustered. Sem SQL Server e sem produção.

O SQL abaixo é o texto que o conector gera. O plano e o tempo ficam para um SHOWPLAN estimado,
somente leitura, do DevOps/DBA. Estes testes não executam a consulta na origem.
"""
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest

from app.bronze.ingestao import _id, data_movimentacao_id
from app.connectors.sqlserver.connector import (
    ConfigSqlServer,
    SqlServerConnector,
    depois_da_chave,
    interpreta_catalogo_pk,
)
from app.connectors.sqlserver.politicas import ORDEM_KEYSET_CC, TabelaRecusada, politica
from app.core.config import Settings, get_settings
from app.core.sql_guard import garante_somente_leitura
from app.ingestion.mapeamentos import MAPEAMENTOS, chave_origem
from app.services import carga_completa as ccomp
from app.services import carga_conector as cc
from app.services.agenda import _tem_indisponivel
from app.services.diagnostico import motivo
from app.silver.tributario import COMPO_ABERTO, id_conta_corrente_legado, situacao_divida

TAB = "Divida_Conta_Corrente"
POL = politica("DIVIDA")
ORDEM = ORDEM_KEYSET_CC
D1 = datetime(2024, 1, 1)
D2 = datetime(2024, 1, 2)


def _conector():
    return SqlServerConnector(ConfigSqlServer(None, 1433, "x", None, None, 5, 1000))


def _linha(dt, seq, codi, sequ, ano, pago=1):
    return {"Data_Movimentacao": dt, "Sequencia": seq, "CODI": codi, "SEQU": sequ, "ANO": ano,
            "Vr_Pago": pago, "Compo": "D"}


def _chave(r):
    return tuple(r[c] for c in ORDEM)


LINHAS = [
    _linha(D1, 1, "A", "00", 2020),
    _linha(D1, 1, "A", "00", 2021),
    _linha(D1, 1, "A", "01", 2020),
    _linha(D1, 1, "B", "00", 2020),
    _linha(D1, 2, "A", "00", 2020),
    _linha(D2, 1, "A", "00", 2019),
    _linha(D2, 1, "A", "00", 2020),
]


def _pagina(linhas, cursor, lote, desde=None):
    sql, params = _conector().montar_pagina_alinhada(POL, TAB, ORDEM, lote, cursor, desde=desde if cursor is None else None)
    assert sql == garante_somente_leitura(sql)
    escolhidas = [r for r in linhas if depois_da_chave(cursor, _chave(r))]
    escolhidas.sort(key=_chave)
    return sql, params, escolhidas[:lote]


def _anda(linhas, lote, cursor=None, desde=None):
    vistos, paginas = [], []
    while True:
        sql, params, pagina = _pagina(linhas, cursor, lote, desde=desde)
        paginas.append((sql, params, pagina))
        vistos.extend(pagina)
        if len(pagina) < lote:
            break
        cursor = _chave(pagina[-1])
    return vistos, paginas


def _catalogo(ordem=ORDEM, anulavel=0, desabilitado=0, pk=1, unico=1, tipo="CLUSTERED"):
    return [(col, i, anulavel, desabilitado, pk, unico, tipo) for i, col in enumerate(ordem, start=1)]


class _Conn:
    def __init__(self, linhas):
        self.linhas, self.sqls = linhas, []

    def cursor(self, as_dict=False):
        conn = self

        class Cur:
            def execute(self, sql, params=None):
                conn.sqls.append((sql, params))
                self.res = conn.linhas

            def fetchmany(self, n):
                return self.res[:n]

        return Cur()

    def rollback(self):
        return None


def test_identidade_de_cinco_colunas_nao_colapsa_duas_datas():
    m = MAPEAMENTOS[("DIVIDA", TAB)]
    assert m["chave"] == ("CODI", "SEQU", "ANO", "Sequencia", "Data_Movimentacao")
    assert m["campo_incremental"] == "Data_Movimentacao"
    assert ccomp.ordem("DIVIDA", "Divida") == ("CODI", "SEQU", "ANO")
    assert ccomp.ordem("DIVIDA", TAB) == ORDEM
    mesmo_dia = {**LINHAS[0], "Data_Movimentacao": "2024-01-01 00:00:00"}
    outro_dia = {**LINHAS[0], "Data_Movimentacao": D2}
    id_a, id_b = _id(m["chave"], LINHAS[0]), _id(m["chave"], outro_dia)
    assert id_a != id_b
    assert id_a == _id(m["chave"], mesmo_dia) == chave_origem(m, mesmo_dia)
    assert id_a.endswith("|2024-01-01T00:00:00.000000") and id_a.count("|") == 4
    assert data_movimentacao_id(D1) == data_movimentacao_id("2024-01-01T00:00:00")
    assert id_conta_corrente_legado("ZZ|00|2024|1") is True
    assert id_conta_corrente_legado(id_a) is False
    assert id_conta_corrente_legado("zzteste-sem-pipe") is False
    assert COMPO_ABERTO == frozenset({"D", "G", "P", "B"})
    compo, liquido, aberto, qualidade = situacao_divida("D", Decimal("10"), Decimal("3"), False)
    assert (compo, liquido, aberto, qualidade) == ("D", Decimal("7"), True, None)
    assert situacao_divida("P", Decimal("1"), Decimal("5"), False)[1] == Decimal("0")


def test_timeouts_e_20047_nao_viram_timeout_da_conta_corrente():
    assert Settings.model_fields["sqlserver_timeout"].default == 5
    assert Settings.model_fields["sqlserver_consulta_timeout"].default == 120
    texto = motivo("OperationalError", 20047, TAB, None)
    assert "20047" in texto and "timeout" not in texto.lower()
    assert "timeout" in motivo("OperationalError", 20009, None, None).lower()


def test_primeira_pagina_e_amostra_ordenam_pela_pk_sem_sort_extra_no_texto():
    sql, params = _conector().montar_pagina_alinhada(POL, TAB, ORDEM, 1000, None)
    assert params == ()
    assert " WHERE " not in sql and "UNION ALL" not in sql and "IS NOT NULL" not in sql
    assert "COUNT" not in sql.upper() and "DISTINCT" not in sql.upper() and "CREATE" not in sql.upper()
    assert sql == (
        "SELECT TOP (1000) [Data_Movimentacao], [Sequencia], [CODI], [SEQU], [ANO], [Vr_Pago], [Compo] "
        "FROM [dbo].[Divida_Conta_Corrente] "
        "ORDER BY [Data_Movimentacao], [Sequencia], [CODI], [SEQU], [ANO]")
    amostra = _conector().montar_select_politica(POL, TAB, limite=1000)
    assert amostra.endswith("ORDER BY [Data_Movimentacao], [Sequencia], [CODI], [SEQU], [ANO]")
    assert "ORDER BY [CODI], [SEQU], [ANO], [Sequencia]" not in amostra


def _ramos_union(sql: str) -> list[str]:
    """Membros do UNION ALL no interior de FROM ( ... ) AS [pagina], sem parser SQL."""
    ini = sql.index(" FROM (") + len(" FROM (")
    fim = sql.rindex(") AS [pagina] ORDER BY ")
    miolo, ramos, inicio, depth, i = sql[ini:fim], [], 0, 0, 0
    marca = " UNION ALL "
    while i < len(miolo):
        if miolo[i] == "(":
            depth += 1
        elif miolo[i] == ")":
            depth -= 1
        if depth == 0 and miolo.startswith(marca, i):
            ramos.append(miolo[inicio:i])
            i += len(marca)
            inicio = i
            continue
        i += 1
    ramos.append(miolo[inicio:])
    return ramos


def _profundidade_order_by(sql: str) -> list[int]:
    achados, depth, i = [], 0, 0
    while i < len(sql):
        if sql[i] == "(":
            depth += 1
        elif sql[i] == ")":
            depth -= 1
        elif sql.startswith("ORDER BY", i) and (i == 0 or not (sql[i - 1].isalnum() or sql[i - 1] == "_")):
            achados.append(depth)
            i += len("ORDER BY")
            continue
        i += 1
    return achados


def test_pagina_com_cursor_e_seek_por_prefixo_sem_residual():
    cursor = (D1, 1, "A", "00", 2020)
    sql, params = _conector().montar_pagina_alinhada(POL, TAB, ORDEM, 1000, cursor)
    assert params == (D1, 1, "A", "00", 2020, D1, 1, "A", "00", D1, 1, "A", D1, 1, D1)
    assert sql.count("UNION ALL") == 4
    assert "nvarchar" not in sql.lower()
    assert "[Data_Movimentacao] > CAST(%s AS datetime)" in sql
    assert ">=" not in sql and "IS NOT NULL" not in sql and "COUNT" not in sql.upper()
    assert sql.endswith("ORDER BY [Data_Movimentacao], [Sequencia], [CODI], [SEQU], [ANO]")
    ramos = _ramos_union(sql)
    tipos = ("datetime", "int", "varchar(22)", "varchar(4)", "int")
    for i, ramo in enumerate(ramos):
        coluna = len(ORDEM) - 1 - i
        iguais = [f"[{c}] = CAST(%s AS {t})" for c, t in zip(ORDEM[:coluna], tipos[:coluna])]
        pred = " AND ".join([*iguais, f"[{ORDEM[coluna]}] > CAST(%s AS {tipos[coluna]})"])
        assert pred in ramo
    py = (datetime, int, str, str, int)
    esperados = [t for coluna in range(len(ORDEM) - 1, -1, -1) for t in py[:coluna + 1]]
    assert [type(v) for v in params] == esperados
    assert len(ramos) == 5
    assert all(r.startswith("(") and r.endswith(")") and r.count("ORDER BY") == 1 for r in ramos)
    resto = sql
    for ramo in ramos:
        resto = resto.replace(ramo, "", 1)
    assert resto.count("ORDER BY") == 1
    assert resto.endswith("ORDER BY [Data_Movimentacao], [Sequencia], [CODI], [SEQU], [ANO]")
    # ORDER BY do ramo fica dentro dos parênteses do ramo (profundidade 2); o único de profundidade 0 é o SELECT externo.
    assert _profundidade_order_by(sql) == [2, 2, 2, 2, 2, 0]
    assert "x'; DROP TABLE y" not in sql
    ruim = (D1, 1, "x'; DROP TABLE y", "00", 2020)
    sql_ruim, params_ruim = _conector().montar_pagina_alinhada(POL, TAB, ORDEM, 5, ruim)
    assert garante_somente_leitura(sql_ruim) and "DROP" not in sql_ruim and ruim[2] in params_ruim
    assert len(_ramos_union(sql_ruim)) == 5 and _profundidade_order_by(sql_ruim) == [2, 2, 2, 2, 2, 0]
    texto, params_texto = _conector().montar_pagina_alinhada(
        POL, TAB, ORDEM, 1000, ("2024-01-01 00:00:00", "1", "A", "00", "2020"))
    assert params_texto[:5] == (D1, 1, "A", "00", 2020)
    assert [type(v) for v in params_texto[:5]] == [datetime, int, str, str, int]
    assert "CAST(%s AS varchar(22))" in texto and "CAST(%s AS varchar(4))" in texto
    assert "CAST(%s AS nvarchar" not in texto


def _literal_tsql(valor) -> str:
    if isinstance(valor, datetime):
        return "'" + valor.strftime("%Y-%m-%dT%H:%M:%S") + "'"
    if isinstance(valor, int) and not isinstance(valor, bool):
        return str(valor)
    if isinstance(valor, str):
        return "N'" + valor.replace("'", "''") + "'"
    raise AssertionError(type(valor))


def _com_literais(sql: str, params: tuple) -> str:
    for valor in params:
        sql = sql.replace("%s", _literal_tsql(valor), 1)
    assert "%s" not in sql and "SET " not in sql
    return sql


def test_script_somente_leitura_espelha_a_pagina_com_cursor():
    """O arquivo para o DevOps é o SQL gerado, com literais sintéticos no lugar de %s. Não roda no SQL Server."""
    cursor = (D1, 1, "A", "00", 2020)
    sql, params = _conector().montar_pagina_alinhada(POL, TAB, ORDEM, 1000, cursor)
    exemplo = _com_literais(sql, params)
    assert len(_ramos_union(exemplo)) == 5
    caminho = Path(__file__).resolve().parents[2] / "docs/evidencias/sigpmf-divida-cc-keyset/validar-sintaxe-pagina-cursor.sql"
    texto = caminho.read_text(encoding="utf-8")
    assert texto.count(exemplo) == 2
    assert "SET PARSEONLY ON;" in texto and "SET PARSEONLY OFF;" in texto
    assert "SET SHOWPLAN_XML ON;" in texto and "SET SHOWPLAN_XML OFF;" in texto
    assert "GO" in texto  # SHOWPLAN_XML precisa ser o único comando do lote


def test_fronteira_mesma_data_desempate_retomada_e_janela():
    vistos, paginas = _anda(LINHAS, 2)
    assert [_chave(r) for r in vistos] == sorted(_chave(r) for r in LINHAS)
    assert len(vistos) == len({_chave(r) for r in vistos})
    chave_bronze = MAPEAMENTOS[("DIVIDA", TAB)]["chave"]
    assert _id(chave_bronze, LINHAS[0]) != _id(chave_bronze, LINHAS[6])
    assert len([r for r in vistos if _id(chave_bronze, r) == _id(chave_bronze, LINHAS[0])]) == 1
    meio = paginas[1]
    assert meio[2][0]["Data_Movimentacao"] == meio[1][0] == D1  # cursor e próxima linha na mesma data
    assert "[Data_Movimentacao] = CAST(%s AS datetime)" in meio[0]
    assert "[ANO] > CAST(%s AS int)" in meio[0] and ">=" not in meio[0]
    ja = vistos[:4]
    resto, _ = _anda(LINHAS, 2, cursor=_chave(ja[-1]))
    assert [_chave(r) for r in ja + resto] == sorted(_chave(r) for r in LINHAS)
    assert not (set(map(_chave, ja)) & set(map(_chave, resto)))
    desde = ("Data_Movimentacao", D2)
    janela = [r for r in LINHAS if r["Data_Movimentacao"] >= D2]
    lidos, pags = _anda(janela, 1, desde=desde)
    assert [_chave(r) for r in lidos] == sorted(_chave(r) for r in janela)
    assert all(r["Data_Movimentacao"] >= D2 for r in lidos)
    assert "WHERE [Data_Movimentacao] >= CAST(%s AS datetime)" in pags[0][0] and pags[0][1] == (D2,)
    assert all(">=" not in sql for sql, _, _ in pags[1:])
    with pytest.raises(TabelaRecusada):
        _conector().montar_pagina_alinhada(POL, TAB, ORDEM, 10, None, desde=("CODI", "A"))


def test_checkpoint_antigo_e_rejeitado_e_o_novo_fecha_o_ciclo():
    with pytest.raises(ccomp.CheckpointIncompativel) as antiga:
        ccomp.abre_cursor("DIVIDA", TAB, ["011", "00", 2024, 1])
    assert "CODI, SEQU, ANO, Sequencia" in str(antiga.value)
    assert "não foi migrada nem reinterpretada" in str(antiga.value)
    assert "011" not in str(antiga.value)
    with pytest.raises(ccomp.CheckpointIncompativel) as sem_versao:
        ccomp.abre_cursor("DIVIDA", TAB, [D1.isoformat(), 1, "A", "00", 2020])
    texto = str(sem_versao.value).casefold()
    assert "sem versão" in texto and "não foi reinterpretado" in texto
    with pytest.raises(ccomp.CheckpointIncompativel):
        ccomp.abre_cursor("DIVIDA", TAB, {"v": 1, "ordem": list(ORDEM), "valores": []})
    with pytest.raises(ccomp.CheckpointIncompativel):
        ccomp.abre_cursor("DIVIDA", TAB, {"v": 2, "ordem": list(ORDEM), "valores": [{"dt": D1.isoformat()}, 1, "A", "00", None]})
    gravado = ccomp.fecha_cursor("DIVIDA", TAB, (D1, 1, "A", "00", 2020))
    assert gravado["v"] == 2 and gravado["ordem"] == list(ORDEM)
    assert ccomp.abre_cursor("DIVIDA", TAB, gravado) == (D1, 1, "A", "00", 2020)
    assert ccomp.abre_cursor("DIVIDA", TAB, None) is None
    assert ccomp.fecha_cursor("SAUDE", "pacientes", (10,)) == [10]
    assert ccomp.abre_cursor("SAUDE", "pacientes", [10]) == (10,)
    assert ccomp.abre_cursor("DIVIDA", "Divida", ["011", "00", 2024]) == ("011", "00", 2024)


def test_catalogo_prova_pk_e_recusa_sem_count():
    assert interpreta_catalogo_pk(_catalogo(), ORDEM)["pk_ok"] is True
    assert interpreta_catalogo_pk(_catalogo(anulavel=True), ORDEM)["pk_ok"] is False
    assert interpreta_catalogo_pk(_catalogo(desabilitado=1), ORDEM)["pk_ok"] is False
    assert interpreta_catalogo_pk(_catalogo(tipo="NONCLUSTERED"), ORDEM)["pk_ok"] is False
    trocada = ("Sequencia", "Data_Movimentacao", "CODI", "SEQU", "ANO")
    ruim = interpreta_catalogo_pk(_catalogo(trocada), ORDEM)
    assert ruim["pk_ok"] is False and "ordem antiga não foi reutilizada" in ruim["motivo"]
    assert interpreta_catalogo_pk([], ORDEM)["pk_ok"] is False
    conn = _Conn(_catalogo())
    visto = _conector().verificar_chave(conn, TAB, POL, ORDEM)
    assert visto["pk_ok"] is True and visto["total"] is None and visto["nulos"] == 0
    assert conn.sqls and "sys.indexes" in conn.sqls[0][0] and "COUNT" not in conn.sqls[0][0].upper()
    assert ccomp.problema_chave(TAB, ORDEM, visto) is None
    falha = _conector().verificar_chave(_Conn(_catalogo(tipo="HEAP")), TAB, POL, ORDEM)
    assert ccomp.problema_chave(TAB, ORDEM, falha)
    bomba = _Conn([])

    def estoura(*_a, **_k):
        raise AssertionError("assinatura não consulta a origem")

    bomba.cursor = estoura
    assert _conector().assinatura(bomba, TAB, POL, "Data_Movimentacao") == [None, "indisponivel"]
    assert _tem_indisponivel({"Divida": [1, "2024-01-01"], TAB: [None, "indisponivel"]})
    with pytest.raises(TabelaRecusada):
        _conector().montar_contagem(POL, TAB)
    with pytest.raises(TabelaRecusada):
        _conector().montar_verificacao_chave(POL, TAB, ORDEM)
    with pytest.raises(TabelaRecusada):
        _conector().montar_assinatura(POL, TAB, "Data_Movimentacao")


def test_outras_politicas_continuam_no_keyset_lexicografico():
    c = _conector()
    sql, params = c.montar_pagina(politica("SAUDE"), "pacientes", ("Matricula",), 10, ("M9",))
    assert "UNION ALL" not in sql and "IS NOT NULL" in sql and params == ("M9",)
    sql, _ = c.montar_pagina(POL, "Divida", ("CODI", "SEQU", "ANO"), 10, None)
    assert "UNION ALL" not in sql and "[CODI] IS NOT NULL" in sql and "COUNT" not in sql.upper()
    sql, _ = c.montar_pagina(politica("IPTU"), "Cadastro", ("CODI",), 10, None)
    assert "UNION ALL" not in sql and "[CODI] IS NOT NULL" in sql
    sql, _ = c.montar_pagina(politica("FINANCEIRO"), "Financeiro_Exercicio", ("Exercicio",), 10, None)
    assert "UNION ALL" not in sql and "IS NOT NULL" in sql and "[Exercicio] >=" in sql


def test_gate2_resolucao_do_banco_nao_usa_a_base_da_politica(monkeypatch, db):
    monkeypatch.delenv("SQLSERVER_DATABASE_DIVIDA", raising=False)
    monkeypatch.setenv("SQLSERVER_DATABASE", "Saude")
    get_settings.cache_clear()
    try:
        _gate2(monkeypatch, db)
    finally:
        get_settings.cache_clear()


def _gate2(monkeypatch, db):
    cfg, fb = ConfigSqlServer.para_conector(get_settings(), "divida")
    assert fb is True and cfg.database == "Saude"
    assert politica("DIVIDA").base == "Divida" and cfg.database != politica("DIVIDA").base
    monkeypatch.setattr(cc, "banco_do_catalogo", lambda _db, _conector: None)
    cfg, fb = cc.config_do_conector("divida", db)
    nivel, msg = cc.descreve_banco("divida", db)
    assert fb is True and cfg.database == "Saude" and nivel == "WARN"
    assert "Saude" in msg and "SQLSERVER_DATABASE (fallback)" in msg and "base Divida" in msg
    assert "não escolhe a conexão" in msg and "SQLSERVER_HOST" not in msg
    monkeypatch.setattr(cc, "banco_do_catalogo", lambda _db, _conector: "Divida")
    cfg, fb = cc.config_do_conector("divida", db)
    _, msg = cc.descreve_banco("divida", db)
    assert fb is False and cfg.database == "Divida" and "catálogo (controle.bases_catalogo)" in msg
    monkeypatch.setenv("SQLSERVER_DATABASE_DIVIDA", "Divida")
    get_settings.cache_clear()
    cfg, fb = ConfigSqlServer.para_conector(get_settings(), "divida")
    _, msg = cc.descreve_banco("divida", db)
    assert fb is False and cfg.database == "Divida" and "SQLSERVER_DATABASE_DIVIDA" in msg
    assert "catálogo" not in msg
    get_settings.cache_clear()
