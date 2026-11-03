"""AU1–AU3: AUDESP (TCE-SP) -> bronze -> prata. Arquivos sintéticos em ISO-8859-1, servidor do TCE simulado (sem rede).

AU-A soma da prata = soma do arquivo (centavo a centavo); AU-B mesmo arquivo de novo não muda nada, arquivo novo troca a
foto do ano (linha removida sai); AU-C cabeçalho diferente recusa sem carregar; AU-D sem mudança no TCE = só HEAD;
AU-F historico_despesa e CPF do credor nunca entram; AU-H download: allowlist, zip com 2 arquivos, 404.
"""
import io
import zipfile
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import delete, func, select

from app.connectors.audesp import arquivos as aq
from app.models.camadas import RegistroBronze
from app.models.controle import Agendamento
from app.models.financas import ArquivoAudesp, Despesa, Receita, RestoAPagar
from app.services import agenda as ag
from app.services import carga_audesp as ca
from app.silver import financas as fi

CFG = aq.ConfigAudesp(base=aq.BASE_PADRAO, municipio="franca", anos=(2026,))


def _csv(tipo: str, linhas: list[list[str]], aspas: bool = False) -> bytes:
    sep = ";"
    fmt = (lambda v: f'"{v}"') if aspas else (lambda v: v)
    texto = "\r\n".join(sep.join(fmt(c) for c in row) for row in [list(aq.COLUNAS[tipo]), *linhas]) + "\r\n"
    return texto.encode(aq.ENCODING)


def _zip(conteudo: bytes, nome: str = "dados.csv", extra: bool = False) -> bytes:
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(nome, conteudo)
        if extra:
            z.writestr("outro.csv", conteudo)
    return b.getvalue()


def _desp(id_, fase, valor, mes="1", ident="CNPJ - PESSOA JURÍDICA - 11222333000181", funcao="SAÚDE",
          aplic="0310 - SAÚDE - GERAL", elem="33903999 - OUTROS SERVIÇOS DE TERCEIROS - PJ"):
    return [id_, "2026", "Franca", "PREFEITURA MUNICIPAL DE FRANCA", mes, "Janeiro", fase, " 10-2026", ident,
            "Fornecedor Exemplo Ltda", "16/01/2026", valor, funcao, "ATENÇÃO BÁSICA", "2001", "Programa", "2201", "Ação",
            "TESOURO", aplic, "PREGÃO ELETRÔNICO", elem, "texto livre com NOME DE PESSOA"]


DESPESAS = [_desp("1", "Empenhado", "1.000,00"), _desp("2", "Valor Liquidado", "400,50"), _desp("3", "Valor Pago", "400,50"),
            _desp("4", "Anulação", "100,00"),
            _desp("5", "Empenhado", "250,25", mes="2", ident="PESSOA FÍSICA - 511418", funcao="EDUCAÇÃO",
                  aplic="0220 - ENSINO FUNDAMENTAL - RECURSOS ESPECÍFICOS", elem="31901101 - VENCIMENTOS")]
RECEITAS = [["10", "2026", "Franca", "PREFEITURA MUNICIPAL DE FRANCA", m, "x", "EXECUTIVO", "01 - TESOURO", "110 - GERAL",
             "00000 - NÃO", "10000000 - Receitas Correntes", "11000000 - Impostos, Taxas e Contribuições de Melhoria",
             "11100000 - Impostos", "11120000 - Impostos sobre o Patrimônio", "11125000 - IPTU", "11125001 - IPTU Principal",
             "11125001 - IPTU - Principal", v] for m, v in (("1", "5.000,00"), ("2", "2.500,10"))]
RECEITAS[1][0] = "11"


def _rp(nr, cred, nome, mes="1", ano="2026"):
    vals = ["0,00"] * 12
    vals[3] = "123,45"  # pago processado
    return ["Franca", "PREFEITURA", "UO", "UE", "PREGÃO", "", "10 - SAÚDE", "301 - ATENÇÃO BÁSICA", "P", "A",
            "01 - TESOURO", "310 - SAÚDE - GERAL", "00000 - X", "3", "3", "90", "33903900 - SERVIÇOS PJ",
            "33903999 - SERVIÇOS PJ", cred, nome, nr, "2025", mes, ano, "31/01/2026", *vals]


RESTOS = [_rp("7-2025", "CNPJ:11222333000181", "Fornecedor Exemplo Ltda"), _rp("8-2025", "CPF:12345678909", "Fulano de Tal"),
          _rp("9-2025", "CNPJ:11222333000181", "Fornecedor Exemplo Ltda", mes="13", ano="2025")]


class TCE:
    """Portal simulado: url -> (Last-Modified, zip). Conta GETs."""

    def __init__(self):
        self.arquivos: dict[str, tuple[str, bytes]] = {}
        self.gets = 0

    def publica(self, tipo, conteudo, lm="Wed, 23 Sep 2026 12:00:00 GMT"):
        self.arquivos[CFG.url(tipo, 2026)] = (lm, _zip(conteudo))

    def cliente(self) -> httpx.Client:
        def h(req: httpx.Request):
            item = self.arquivos.get(str(req.url))
            if item is None:
                return httpx.Response(404)
            lm, corpo = item
            if req.method == "HEAD":
                return httpx.Response(200, headers={"last-modified": lm, "content-length": str(len(corpo))})
            self.gets += 1
            return httpx.Response(200, content=corpo, headers={"last-modified": lm})
        return httpx.Client(transport=httpx.MockTransport(h))


def _limpa(db):
    db.rollback()
    for m in (Despesa, Receita, RestoAPagar, ArquivoAudesp):
        db.execute(delete(m))
    db.execute(delete(RegistroBronze).where(RegistroBronze.sistema_origem == aq.SISTEMA))
    db.commit()


@pytest.fixture
def tce(db):
    _limpa(db)
    t = TCE()
    t.publica("despesas", _csv("despesas", DESPESAS))
    t.publica("receitas", _csv("receitas", RECEITAS))
    t.publica("restos_a_pagar", _csv("restos_a_pagar", RESTOS, aspas=True))
    yield t
    _limpa(db)


def _soma(db, col, *filtros):
    return db.scalar(select(func.coalesce(func.sum(col), 0)).where(*filtros))


def test_au_projecao_e_contrato():
    assert fi.valor("1.234,56") == Decimal("1234.56") and fi.valor("") == Decimal("0")
    assert fi.codigo("0310 - SAÚDE - GERAL", 4) == ("0310", "SAÚDE - GERAL")
    assert fi.credor("CNPJ - PESSOA JURÍDICA - 11222333000181") == ("PJ", "11222333000181", None)
    assert fi.credor("CNPJ - PESSOA JURÍDICA - 12.ABC.345/01DE-35") == ("PJ", "12ABC34501DE35", None)  # CN1
    assert fi.credor("PESSOA FÍSICA - 511418") == ("PF", None, "511418")
    assert fi.credor("IDENTIFICAÇÃO ESPECIAL - SEM CPF/CNPJ - 515486817") == ("especial", None, "515486817")
    assert fi.despesa(dict(zip(aq.COLUNAS["despesas"], DESPESAS[3], strict=True)), 2026)["valor"] == Decimal("-100.00")
    assert fi.natureza_do_elemento("31901101") == "pessoal"
    with pytest.raises(aq.ArquivoInvalido, match="coluna 'vl_despesa' ausente"):
        aq.linhas(io.StringIO(";".join(c for c in aq.COLUNAS["despesas"] if c != "vl_despesa") + "\n"))
    with pytest.raises(aq.ArquivoInvalido, match="exatamente um"):
        aq.abre_csv(_zip(_csv("receitas", RECEITAS), extra=True))
    with pytest.raises(PermissionError):
        aq.ConfigAudesp(base="https://exemplo.com/files", municipio="franca", anos=(2026,)).url("receitas", 2026)


def test_au_zip_que_mente_o_tamanho(monkeypatch):
    monkeypatch.setattr(aq, "MAX_CSV", 200)
    with pytest.raises(aq.ArquivoInvalido, match="limite"):
        _t, gen = aq.linhas(aq.abre_csv(_zip(_csv("despesas", DESPESAS))))
        list(gen)


def test_au_carga_soma_idempotencia_e_lgpd(db, tce):
    ex = ca.executar(db, "teste", CFG, cliente=tce.cliente())
    assert ex.status == "sucesso", ex.mensagem
    # AU-A: soma por fase = soma do arquivo; anulação negativa
    assert _soma(db, Despesa.valor, Despesa.fase == "empenhado") == Decimal("1250.25")
    assert _soma(db, Despesa.valor, Despesa.fase == "anulado") == Decimal("-100.00")
    assert _soma(db, Receita.valor) == Decimal("7500.10")
    assert db.scalar(select(func.count()).select_from(RestoAPagar)) == 3
    # restos: o arquivo de 2026 traz o encerramento de 2025 (mês 13) e fica como ano 2026
    assert set(db.scalars(select(RestoAPagar.ano_arquivo))) == {2026}
    # AU-F: histórico e CPF fora da bronze e da prata
    dados = [r.dados for r in db.scalars(select(RegistroBronze).where(RegistroBronze.sistema_origem == aq.SISTEMA))]
    assert dados and all("historico_despesa" not in d for d in dados)
    assert "12345678909" not in str(dados)
    pf = db.scalar(select(RestoAPagar).where(RestoAPagar.nr_empenho == "8-2025"))
    assert pf.credor_tipo == "PF" and pf.credor_cnpj is None and pf.credor_nome is None
    assert db.scalar(select(Despesa).where(Despesa.id_origem == "5")).credor_codigo_tce == "511418"
    reg = db.scalar(select(ArquivoAudesp).where(ArquivoAudesp.tipo == "despesas"))
    assert (reg.ano, reg.linhas, reg.ultimo_mes, reg.origem) == (2026, 5, 2, "tce")
    # AU-D: nada mudou no TCE -> só HEAD, nenhum GET
    gets = tce.gets
    ex2 = ca.executar(db, "teste", CFG, cliente=tce.cliente())
    assert ex2.status == "sem_novidades" and tce.gets == gets
    # republicado com a mesma data nova mas conteúdo igual -> baixa, compara o SHA e não recarrega
    tce.publica("receitas", _csv("receitas", RECEITAS), lm="Thu, 24 Sep 2026 12:00:00 GMT")
    ex3 = ca.executar(db, "teste", CFG, cliente=tce.cliente())
    assert ex3.status == "sem_novidades" and tce.gets == gets + 1
    # AU-B: arquivo novo troca a foto do ano: linha 2 sai, linha 6 entra
    novas = [d for d in DESPESAS if d[0] != "2"] + [_desp("6", "Valor Liquidado", "10,00", mes="3")]
    tce.publica("despesas", _csv("despesas", novas), lm="Fri, 23 Oct 2026 12:00:00 GMT")
    ex4 = ca.executar(db, "teste", CFG, cliente=tce.cliente())
    assert ex4.status == "sucesso", ex4.mensagem
    assert sorted(db.scalars(select(Despesa.id_origem))) == ["1", "3", "4", "5", "6"]
    assert _soma(db, Despesa.valor, Despesa.fase == "liquidado") == Decimal("10.00")
    assert db.scalar(select(ArquivoAudesp.ultimo_mes).where(ArquivoAudesp.tipo == "despesas")) == 3


def test_au_arquivo_ruim_nao_carrega_e_os_outros_seguem(db, tce):
    ruim = ";".join(c for c in aq.COLUNAS["receitas"] if c != "ds_tipo").encode(aq.ENCODING) + b"\r\n1;2\r\n"
    tce.arquivos[CFG.url("receitas", 2026)] = ("x", _zip(ruim))
    del tce.arquivos[CFG.url("restos_a_pagar", 2026)]  # ainda não publicado (404)
    ex = ca.executar(db, "teste", CFG, cliente=tce.cliente())
    assert ex.status == "parcial" and "receitas 2026" in ex.mensagem
    assert db.scalar(select(func.count()).select_from(Receita)) == 0  # AU-C: nada da receita ruim entrou
    assert db.scalar(select(func.count()).select_from(Despesa)) == 5
    assert db.scalar(select(func.count()).select_from(RegistroBronze).where(
        RegistroBronze.sistema_origem == aq.SISTEMA, RegistroBronze.tabela == "receitas")) == 0


def test_au_upload_e_permissoes(client, tokens, db, tce):
    corpo = _csv("receitas", RECEITAS)
    assert client.post("/api/audesp/arquivo", content=corpo, headers=tokens["leitura"]).status_code == 403
    r = client.post("/api/audesp/arquivo", content=corpo, headers={**tokens["admin"], "Content-Type": "text/csv"})
    assert r.status_code == 200, r.text
    assert "receitas 2026 até o mês 2" in r.json()["mensagem"]
    ruim = client.post("/api/audesp/arquivo", content=b"a;b\r\n1;2\r\n", headers=tokens["admin"])
    assert ruim.status_code == 422 and "não parece ser do AUDESP" in ruim.json()["detail"]
    lista = client.get("/api/audesp/arquivos", headers=tokens["leitura"]).json()
    assert [(a["tipo"], a["ano"], a["origem"], a["linhas"]) for a in lista["arquivos"]] == [("receitas", 2026, "upload", 2)]


def test_au_agenda_roda_o_audesp(db, tce, monkeypatch):
    monkeypatch.setattr(aq, "cliente_http", tce.cliente)
    monkeypatch.setattr(aq.ConfigAudesp, "do_ambiente", classmethod(lambda cls, hoje=None: CFG))
    item = db.scalar(select(Agendamento).where(Agendamento.conector == ag.AUDESP))
    assert item is not None and item.ativo is False and item.horario == "05:00"  # 0038: nasce desligado
    assert ag.executar(db, item) == "sucesso"
    db.refresh(item)
    assert "despesas 2026 até o mês 2" in (item.ultima_mensagem or "")
    assert ag.executar(db, item) == "sucesso" and "Nenhum arquivo novo" in (item.ultima_mensagem or "")
    assert any(i["conector"] == "audesp" and i["nome"].startswith("AUDESP") for i in ag.estado(db)["itens"])


def test_au_carregar_agora_pela_tela_e_orfa(client, tokens, db, tce, monkeypatch):
    from datetime import UTC, datetime, timedelta

    from app.models.controle import Execucao
    monkeypatch.setattr(aq, "cliente_http", tce.cliente)
    monkeypatch.setattr(aq.ConfigAudesp, "do_ambiente", classmethod(lambda cls, hoje=None: CFG))
    monkeypatch.setattr(ca, "disparar", lambda ex_id, forcar=False: ca.rodar(ex_id, forcar))  # síncrono no teste
    assert client.post("/api/audesp/carregar", json={}, headers=tokens["operador"]).status_code == 403
    r = client.post("/api/audesp/carregar", json={}, headers=tokens["admin"])
    assert r.status_code == 202, r.text
    d = client.get("/api/audesp/arquivos", headers=tokens["leitura"]).json()
    assert d["anos"] == [2026] and d["em_andamento"] is None
    assert d["ultima_execucao"]["status"] == "sucesso" and "despesas 2026 até o mês 2" in d["ultima_execucao"]["mensagem"]
    assert {(a["tipo"], a["ultimo_mes"]) for a in d["arquivos"]} == {("despesas", 2), ("receitas", 2), ("restos_a_pagar", 1)}
    # forcar: baixa de novo, mas o conteúdo igual é pulado pelo SHA do CSV
    gets = tce.gets
    client.post("/api/audesp/carregar", json={"forcar": True}, headers=tokens["admin"])
    assert tce.gets == gets + 3
    assert client.get("/api/audesp/arquivos", headers=tokens["leitura"]).json()["ultima_execucao"]["status"] == "sem_novidades"
    # órfã: em andamento sem sinal de vida há mais de 30 min não trava a próxima carga
    velha = Execucao(tipo=ca.TIPO, sistema=aq.SISTEMA, status="em_andamento", usuario="x", modo="incremental",
                     atualizado_em=datetime.now(UTC) - timedelta(hours=2))
    db.add(velha)
    db.commit()
    assert client.post("/api/audesp/carregar", json={}, headers=tokens["admin"]).status_code == 202
    db.refresh(velha)
    assert velha.status == "interrompida"
    # em andamento de verdade (recente) -> 409
    db.add(Execucao(tipo=ca.TIPO, sistema=aq.SISTEMA, status="em_andamento", usuario="x", modo="incremental",
                    atualizado_em=datetime.now(UTC)))
    db.commit()
    r = client.post("/api/audesp/carregar", json={}, headers=tokens["admin"])
    assert r.status_code == 409 and "em andamento" in r.json()["detail"]
    db.execute(delete(Execucao).where(Execucao.tipo == ca.TIPO, Execucao.status == "em_andamento"))
    db.commit()


def test_au5_painel_financas(client, tokens, db, tce):
    vazio = client.get("/api/financas/painel", headers=tokens["leitura"]).json()
    assert vazio["atual"] is None and vazio["disponivel"]["anos"] == []
    assert ca.executar(db, "teste", CFG, cliente=tce.cliente()).status == "sucesso"
    d = client.get("/api/financas/painel", headers=tokens["leitura"]).json()
    assert d["filtro"] == {"ano": 2026, "mes_ate": 2, "orgao": None, "funcao": None, "fonte": None}
    a = d["atual"]
    # empenhado líquido = 1000 + 250,25 - 100 (anulação); liquidado 400,50; receita 7.500,10
    assert (a["empenhado"], a["liquidado"], a["pago"], a["receita"]) == (1150.25, 400.5, 400.5, 7500.1)
    assert a["resultado"] == pytest.approx(7099.6) and a["pessoas_fisicas"] == 250.25 and a["restos_pagos"] == 246.9  # 2 linhas de 2026; o mês 13 de 2025 fica fora
    assert d["anterior"] is None  # 2025 não carregado
    assert [x["dim"] for x in d["credores_pj"]] == ["Fornecedor Exemplo Ltda"]  # PF não entra no ranking
    assert "511418" not in str(d) and "Fulano" not in str(d)
    assert {x["periodo"] for x in d["mensal"]["receita"]} == {"2026-01", "2026-02"}
    # até janeiro e só Educação: receita/resultado/restos somem (não têm função)
    e = client.get("/api/financas/painel", params={"mes_ate": 1, "funcao": "EDUCAÇÃO"}, headers=tokens["leitura"]).json()["atual"]
    assert e["empenhado"] == 0 and e["receita"] is None and e["resultado"] is None and e["restos_pagos"] is None
    s = client.get("/api/financas/painel", params={"funcao": "SAÚDE"}, headers=tokens["leitura"]).json()
    assert s["atual"]["empenhado"] == 900.0 and [x["dim"] for x in s["por_funcao"]] == ["SAÚDE"]
    assert client.get("/api/financas/painel").status_code == 401
