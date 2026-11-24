"""Cobertura de planos: faixa da ANS, agregação, carga com arquivo sintético, painel, estimativa por bairro e erros."""
import io
import zipfile

import httpx
import pytest
from sqlalchemy import text

CAB = "ID_CMPT_MOVEL;NM_RAZAO_SOCIAL;CD_MUNICIPIO;NM_MUNICIPIO;DE_FAIXA_ETARIA;DE_CONTRATACAO_PLANO;COBERTURA_ASSIST_PLAN;QT_BENEFICIARIO_ATIVO;QT_BENEFICIARIO_ADERIDO;QT_BENEFICIARIO_CANCELADO\n"


def _zip(linhas: str, cab: str = CAB) -> bytes:
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("x.csv", (cab + linhas).encode())
    return b.getvalue()


LINHAS = ("2026-08;OP A;351620;Franca;Menos de 1 ano;Coletivo Empresarial;Médico-hospitalar;10;1;0\n"
          "2026-08;OP A;351620;Franca;1 a 4 anos;Coletivo Empresarial;Médico-hospitalar;30;2;1\n"
          "2026-08;OP B;351620;Franca;60 a 64 anos;Individual ou Familiar;Médico-hospitalar;60;0;3\n"
          "2026-08;OP B;351620;Franca;60 a 64 anos;Individual ou Familiar;Odontológico;7;0;0\n"
          "2026-08;OP C;351620;Franca;Idade estranha;Individual ou Familiar;Médico-hospitalar;5;0;0\n"
          "2026-08;OP A;354340;Ribeirão Preto;20 a 24 anos;Coletivo Empresarial;Médico-hospitalar;800;0;0\n")
IBGE_POP = [{"resultados": [{"series": [
    {"localidade": {"id": "3516200"}, "serie": {"2026": "1000"}}, {"localidade": {"id": "3543402"}, "serie": {"2026": "2000"}}]}]}]


def _censo(valores: dict[int, str]) -> list:
    from app.connectors.ans.arquivos import IDADE_CENSO
    return [{"resultados": [{"classificacoes": [{"id": "2", "categoria": {"6794": "Total"}}, {"id": "287", "categoria": {str(c): ""}}],
                             "series": [{"serie": {"2022": valores.get(c, "0")}}]} for c in IDADE_CENSO]}]


IBGE_CENSO = {93070: "100", 93095: "400"}


def _cliente(falha_mes=False):
    def rota(req: httpx.Request) -> httpx.Response:
        u = str(req.url)
        if u.endswith("-024/"):
            return httpx.Response(200, text='<a href="202608/">202608/</a><a href="dicionario/">d</a>')
        if u.endswith("2026_08.zip"):
            return httpx.Response(500) if falha_mes else httpx.Response(200, content=_zip(LINHAS))
        if "/6579/" in u:
            return httpx.Response(200, json=IBGE_POP)
        if "/9514/" in u:
            return httpx.Response(200, json=_censo(IBGE_CENSO))
        return httpx.Response(404)
    return httpx.Client(transport=httpx.MockTransport(rota))


def test_faixa_junta_grupos_da_ans():
    from app.connectors.ans.arquivos import faixa_padrao
    assert faixa_padrao("Menos de 1 ano") == faixa_padrao("1 a 4 anos") == "0 a 4 anos"
    assert faixa_padrao("15 a 17 anos") == "15 a 19 anos" and faixa_padrao("80 anos ou mais") == "80 anos ou mais"
    assert faixa_padrao("Inconsistente") is None


def test_cabecalho_errado_e_host_fora_da_lista(tmp_path):
    from app.connectors.ans import arquivos as a
    p = tmp_path / "x.zip"
    p.write_bytes(_zip("1;2\n", cab="A;B\n"))
    with pytest.raises(a.ArquivoAnsInvalido, match="Colunas ausentes"):
        list(a.linhas_do_zip(p))
    with pytest.raises(a.ArquivoAnsInvalido):
        a._confere_host("https://exemplo.com/x.zip")


def test_carga_painel_e_estimativa_por_bairro(client, tokens, banco, monkeypatch):
    from app.core.db import SessionLocal
    from app.gold import cubos
    from app.services import carga_ans
    h = tokens["leitura"]
    assert client.get("/api/cobertura/painel", headers=h).json()["atual"] is None
    db = SessionLocal()
    ex = carga_ans.executar(db, "teste", cliente=_cliente())
    assert ex.status == "sucesso" and ex.lidos == 1
    assert db.execute(text("SELECT medica, odonto FROM silver.ans_municipio WHERE cd_municipio = 351620")).one() == (105, 7)
    # Reconfere o último mês sem duplicar
    assert carga_ans.executar(db, "teste", cliente=_cliente()).status == "sucesso"
    assert db.scalar(text("SELECT sum(ativos) FROM silver.ans_beneficiarios WHERE cobertura = 'medica'")) == 105
    # Pessoas de um bairro, com idades das duas faixas
    db.execute(text("DELETE FROM silver.enderecos"))
    db.execute(text("INSERT INTO silver.cidadaos (cidadao_id, nome, data_nascimento, falecido) SELECT 'cob' || g, 'x', CURRENT_DATE - interval '62 years', false FROM generate_series(1, 150) g ON CONFLICT DO NOTHING"))
    db.execute(text("INSERT INTO silver.enderecos (cidadao_id, bairro, sistema, mais_recente) SELECT 'cob' || g, 'Bairro Cobertura', 'T', true FROM generate_series(1, 150) g"))
    db.commit()
    cubos.reconstruir(db)
    d = client.get("/api/cobertura/painel", headers=h).json()
    assert d["atual"]["beneficiarios"] == 105 and d["atual"]["taxa"] == 10.5 and d["atual"]["odonto"] == 7
    faixa = {x["dim"]: x["valor"] for x in d["taxa_faixa"]}
    # 40 beneficiários ÷ (100 do Censo × 1000/500 da população) e 60 ÷ (400 × 2)
    assert faixa == {"0 a 4 anos": 20.0, "60 a 64 anos": 7.5}
    assert d["operadoras"][0] == {"periodo": "", "dim": "OP B", "valor": 60.0}
    b = {x["bairro"]: x for x in d["bairros"]}["Bairro Cobertura"]
    assert b["pessoas"] == 150 and b["taxa_estimada"] == 7.5
    db.close()


def test_falha_de_um_mes_vira_parcial_e_so_admin_dispara(client, tokens, banco, monkeypatch):
    from app.core.db import SessionLocal
    from app.services import carga_ans
    db = SessionLocal()
    ex = carga_ans.executar(db, "teste", cliente=_cliente(falha_mes=True))
    assert ex.status == "parcial" and "2026-08" in ex.mensagem
    db.close()
    assert client.post("/api/cobertura/carregar", headers=tokens["leitura"]).status_code == 403
    e = client.get("/api/cobertura/estado", headers=tokens["leitura"]).json()
    assert e["meses"] == 1 and e["ultima_execucao"]["status"] == "parcial"


def test_censo_sem_valor_nao_grava_e_avisa():
    from app.connectors.ans import arquivos as a
    cli = httpx.Client(transport=httpx.MockTransport(lambda _r: httpx.Response(200, json=_censo({93070: "..."}))))
    with pytest.raises(ValueError, match="todas as faixas"):
        a.censo_por_faixa(cli)
