"""RH1/RH2: Portal da Transparência (simulado, sem rede) -> silver.rh_remuneracao -> painel só com agregados."""
import csv
import io
from datetime import date
from urllib.parse import parse_qs

import httpx
import pytest
from sqlalchemy import delete, func, select, update

from app.connectors.portal_rh import servidores as sv
from app.models.controle import Execucao
from app.models.rh import MesRh, Remuneracao
from app.services import carga_rh as cr
from app.silver import rh

CFG = sv.ConfigPortalRh(url=sv.URL_PADRAO, ano_inicial=2025, pausa_s=0)
HOJE = date(2025, 3, 15)
HTML = ('<form id="formFiltro"><button id="formFiltro:j_idt120" name="formFiltro:j_idt120" type="submit"></button>'
        '<input name="javax.faces.ViewState" value="vs-1" /></form>')
PARCIAL = ('<?xml version="1.0"?><partial-response><changes><update id="j_id1:javax.faces.ViewState:0">'
           '<![CDATA[vs-2]]></update></changes></partial-response>')


def _linha(mat, nome="SERVIDOR TESTE", cargo="PROFESSOR", sec="SECRETARIA MUNICIPAL DE EDUCACAO", bruto="5.000,00", adm="10/01/2025",
           dem="", sit="Ativo"):
    return [sit, str(mat), nome, cargo, "3.000,00", bruto, "1.000,00", "4.000,00", "", adm, sec, "40", "C1", "", dem]


def _csv(linhas) -> bytes:
    b = io.StringIO()
    w = csv.writer(b, quoting=csv.QUOTE_ALL)
    w.writerow(sv.COLUNAS)
    w.writerows(linhas)
    return b.getvalue().encode("utf-8")


class Portal:
    """Imita a página: guarda ano e mês do formulário e devolve o CSV daquele mês. `meses`: (ano, mes) -> linhas | bytes."""

    def __init__(self, meses):
        self.meses, self.exportados, self.fora = meses, [], False

    def cliente(self):
        def h(req: httpx.Request):
            if self.fora:
                return httpx.Response(503)
            if req.method == "GET":
                return httpx.Response(200, text=HTML)
            d = {k: v[0] for k, v in parse_qs(req.content.decode(), keep_blank_values=True).items()}
            if "formFiltro:csv" not in d:
                assert d["javax.faces.ViewState"] in ("vs-1", "vs-2")
                return httpx.Response(200, text=PARCIAL)
            ch = (int(d["formFiltro:cbmAno:cmbInput_input"]), int(d["formFiltro:cmbMes:cmbInput_input"]))
            self.exportados.append(ch)
            if ch not in self.meses:
                return httpx.Response(200, text="<html>sem resultado</html>", headers={"content-type": "text/html"})
            v = self.meses[ch]
            return httpx.Response(200, content=v if isinstance(v, bytes) else _csv(v), headers={"content-type": "text/csv;charset=UTF-8"})
        return httpx.Client(transport=httpx.MockTransport(h))


def _limpa(db):
    db.rollback()
    db.execute(delete(Remuneracao))
    db.execute(delete(MesRh))
    db.execute(update(Execucao).where(Execucao.tipo == cr.TIPO, Execucao.status == "em_andamento").values(status="cancelada"))
    db.commit()


@pytest.fixture
def limpo(db):
    _limpa(db)
    yield db
    _limpa(db)


def test_rh1_secretaria_padronizada_ao_longo_dos_anos():
    for orig, nome in (("SECRETARIA DE EDUCAÇÃO", "Educação"), ("SECRETARIA MUNICIPAL DE EDUCACAO", "Educação"),
                       ("SECRETARIA DE RECURSOS HUMANOS", "Administração e RH"), ("SECRETARIA MUNICIPAL DE ADMINISTRACAO E RECURSOS HUMANOS", "Administração e RH"),
                       ("SECRETARIA DE SERVIÇOS E MEIO AMBIENTE", "Meio Ambiente e Serviços"), ("SECRETARIA MUNICIPAL DE MEIO AMBIENTE", "Meio Ambiente e Serviços"),
                       ("PROCURADORIA GERAL MUNICIPIO", "Procuradoria"), ("APOSENTADOS", "Aposentados"), ("", rh.SEM_SECRETARIA),
                       ("SECRETARIA DE ESPORTES, ARTE, CULTURA E LAZER", "Esporte e Cultura"), ("SECRETARIA MUNICIPAL DE ESPORTE E CULTURA", "Esporte e Cultura"),
                       ("SECRETARIA DE PLANEJAMENTO URBANO", "Infraestrutura e Urbanismo")):
        assert rh.secretaria(orig) == nome
    assert rh.secretaria("ORGAO NOVO QUALQUER") == "Orgao Novo Qualquer"  # desconhecida: legível, sem inventar
    assert rh.GRUPOS["Pensionistas"] == "inativos" and rh.GRUPOS["Cedidos"] == "outros"


def test_rh1_calendario_e_host():
    assert cr.meses_ate(2024, date(2025, 2, 1)) == [(2024, m) for m in range(1, 14)] + [(2025, 1), (2025, 2)]
    assert (2025, 13) in cr.meses_ate(2025, date(2025, 11, 1))
    assert cr.reconfere(2025, 3, HOJE) and cr.reconfere(2025, 1, HOJE) and not cr.reconfere(2024, 6, HOJE) and cr.reconfere(2024, 13, HOJE)
    with pytest.raises(PermissionError):
        sv.baixa_mes(httpx.Client(), "https://exemplo.com/x", 2025, 1)
    assert sv.cliente_http() is not None  # o intermediário da Certum carrega


def test_rh1_carga_retomada_mes_vazio_e_troca(limpo):
    db = limpo
    linhas = [_linha(i) for i in range(1, 8)]
    p = Portal({(2025, 1): linhas, (2025, 2): linhas + [_linha(8)]})
    ex = cr.executar(db, "teste", CFG, cliente=p.cliente(), hoje=HOJE)
    assert ex.status == "sucesso" and ex.mensagem == "2 meses carregados (01/2025 a 02/2025)." and ex.lidos == 15
    assert db.scalar(select(func.count()).select_from(Remuneracao)) == 15
    r = db.scalar(select(Remuneracao).where(Remuneracao.ano == 2025, Remuneracao.mes == 1, Remuneracao.matricula == "1"))
    assert (r.nome, r.secretaria, r.grupo, float(r.vencimentos), r.admissao) == ("SERVIDOR TESTE", "Educação", "ativa", 5000.0, date(2025, 1, 10))
    assert db.scalar(select(MesRh.linhas).where(MesRh.ano == 2025, MesRh.mes == 3)) == 0  # março: o portal ainda não tem
    # segunda carga: tudo dentro dos últimos 3 meses é conferido, conteúdo igual não regrava
    p.exportados.clear()
    ex = cr.executar(db, "teste", CFG, cliente=p.cliente(), hoje=HOJE)
    assert ex.status == "sem_novidades" and set(p.exportados) == {(2025, 1), (2025, 2), (2025, 3)}
    # mês antigo já carregado é pulado; forcar confere
    p.exportados.clear()
    cr.executar(db, "teste", CFG, cliente=p.cliente(), hoje=date(2026, 3, 1))
    assert (2025, 1) not in p.exportados and (2025, 13) in p.exportados
    # o portal corrige fevereiro: a foto do mês é trocada, sem duplicar
    p.meses[(2025, 2)] = [_linha(1)]
    ex = cr.executar(db, "teste", CFG, cliente=p.cliente(), hoje=HOJE)
    assert ex.status == "sucesso"
    assert db.scalar(select(func.count()).select_from(Remuneracao).where(Remuneracao.mes == 2)) == 1


def test_rh1_formato_mudou_e_portal_fora(limpo):
    db = limpo
    p = Portal({(2025, 1): [_linha(1)], (2025, 2): b'"Matricula","Nome"\n"1","X"\n'})
    ex = cr.executar(db, "teste", CFG, cliente=p.cliente(), hoje=HOJE)
    assert ex.status == "parcial" and "02/2025" in ex.mensagem
    assert db.scalar(select(func.count()).select_from(MesRh).where(MesRh.mes == 2)) == 0  # o mês ruim não fica marcado
    # valor fora do padrão: desfaz o mês inteiro
    p2 = Portal({(2025, 2): [_linha(1), _linha(2, bruto="abc")]})
    assert cr.executar(db, "teste", CFG, cliente=p2.cliente(), hoje=HOJE).status in ("parcial", "falha")
    assert db.scalar(select(func.count()).select_from(Remuneracao).where(Remuneracao.mes == 2)) == 0
    # portal fora: para depois de 3 falhas seguidas, sem varrer o calendário todo
    fora = Portal({})
    fora.fora = True
    longo = sv.ConfigPortalRh(url=sv.URL_PADRAO, ano_inicial=2020, pausa_s=0)
    ex = cr.executar(db, "teste", longo, cliente=fora.cliente(), hoje=HOJE)
    assert ex.status == "falha"
    assert ex.mensagem.count("/") == 3  # 3 meses citados


def test_rh2_painel_so_agregados_e_supressao(client, tokens, limpo):
    db = limpo
    SIG = "NOME-SECRETO-XYZ"
    edu = [_linha(1000 + i, nome=SIG, cargo="PROFESSOR", adm="10/01/2010") for i in range(10)]
    sau = [_linha(2000 + i, nome=SIG, cargo="MEDICO", sec="SECRETARIA MUNICIPAL DE SAUDE", bruto="15.000,00", adm="05/02/2025") for i in range(6)]
    zerados = [_linha(5000 + i, nome=SIG, bruto="0,00") for i in range(20)]  # até 2015 o portal lista matrícula sem pagamento
    pequena = [_linha(3000, nome=SIG, cargo="PREFEITO", sec="GABINETE DO PREFEITO", bruto="30.000,00", dem="20/02/2025", sit="Demitido")]
    apos = [_linha(4000 + i, nome=SIG, cargo="PROFESSOR", sec="APOSENTADOS", adm="10/01/1990", sit="") for i in range(5)]
    p = Portal({(2024, 2): edu, (2025, 2): edu + sau + pequena + apos + zerados, (2025, 13): edu})
    cfg = sv.ConfigPortalRh(url=sv.URL_PADRAO, ano_inicial=2024, pausa_s=0)
    assert cr.executar(db, "teste", cfg, cliente=p.cliente(), hoje=date(2025, 12, 1)).status == "sucesso"
    assert db.scalar(select(Remuneracao.nome).limit(1)) == SIG  # o nome fica na prata para o cruzamento
    h = tokens["leitura"]
    r = client.get("/api/rh/painel", headers=h)
    assert r.status_code == 200
    d = r.json()
    assert SIG not in r.text and "1000" not in [x["dim"] for x in d["por_cargo_servidores"]]  # nem nome nem matrícula
    assert d["filtro"]["ano"] == 2025 and d["filtro"]["mes"] == 2  # último mês comum (o 13º não é o padrão)
    a = d["atual"]
    assert a["servidores"] == 22 and a["bruto"] == 5000 * 15 + 15000 * 6 + 30000 and a["desligamentos"] == 1 and a["admissoes"] == 6
    assert a["mediana"] == 5000 and d["anterior"]["servidores"] == 10  # os 20 zerados ficam fora de quadro, média e mediana
    assert db.scalar(select(func.count()).select_from(Remuneracao).where(Remuneracao.ano == 2025, Remuneracao.mes == 2)) == 42  # ...mas na prata
    assert d["disponivel"]["anos_sem_13"] == [2024]
    sec = {x["dim"]: x["valor"] for x in d["por_secretaria_servidores"]}
    assert sec == {"Educação": 10, "Saúde": 6, "Aposentados": 5}  # Gabinete (1 pessoa) suprimido
    assert "PREFEITO" not in [x["dim"] for x in d["por_cargo_media"]]
    assert {x["dim"]: x["valor"] for x in d["por_faixa"]} == {"R$ 5 a 8 mil": 15, "R$ 12 a 20 mil": 6}  # 30 mil: 1 pessoa, suprimida
    # filtros
    s = client.get("/api/rh/painel", params={"secretaria": "Saúde"}, headers=h).json()
    assert s["atual"]["servidores"] == 6 and s["atual"]["media"] == 15000
    assert len(s["por_secretaria_servidores"]) == 3  # as barras continuam todas, para trocar com um clique
    g = client.get("/api/rh/painel", params={"grupo": "inativos"}, headers=h).json()
    assert g["atual"]["servidores"] == 5
    # recorte pequeno: nada de valor sai
    peq = client.get("/api/rh/painel", params={"secretaria": "Gabinete do Prefeito"}, headers=h).json()
    assert peq["atual"] == {"servidores": 1, "suprimido": True} and peq["por_cargo_media"] == [] and peq["mensal"]["bruto"] == []
    # 13º à parte: fora da série mensal, dentro do total do ano
    d13 = client.get("/api/rh/painel", params={"ano": 2025, "mes": 13}, headers=h).json()
    assert d13["atual"]["servidores"] == 10 and "admissoes" not in d13["atual"]
    assert all(not x["periodo"].endswith("-13") for x in d["mensal"]["bruto"])
    assert {x["periodo"]: x["valor"] for x in d["anual"]["bruto"]}["2025"] == a["bruto"] + 50000
    assert client.get("/api/rh/painel").status_code == 401


def test_rh1_carregar_pela_tela(client, tokens, limpo, monkeypatch):
    p = Portal({(2025, 1): [_linha(i) for i in range(6)]})
    monkeypatch.setattr(sv, "cliente_http", p.cliente)
    monkeypatch.setattr(sv.ConfigPortalRh, "do_ambiente", classmethod(lambda cls: CFG))
    monkeypatch.setattr(cr, "disparar", lambda ex_id, forcar=False: cr.rodar(ex_id, forcar))
    assert client.post("/api/rh/carregar", json={}, headers=tokens["operador"]).status_code == 403
    assert client.post("/api/rh/carregar", json={}, headers=tokens["admin"]).status_code == 202
    e = client.get("/api/rh/estado", headers=tokens["leitura"]).json()
    assert e["em_andamento"] is None and e["ultima_execucao"]["status"] == "sucesso"
    assert e["meses"] == 1 and e["linhas"] == 6 and e["primeiro"] == [2025, 1]
