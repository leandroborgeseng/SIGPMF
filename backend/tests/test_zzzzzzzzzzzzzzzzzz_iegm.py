"""IEG-M (TCE-SP): importação anual por CSV e painel. Arquivo real de 2014–2024 (Franca e estado) como fixture."""
import os

from sqlalchemy import delete, func, select

from app.models.iegm import FaixaIegm
from app.services import iegm

ARQ = os.path.join(os.path.dirname(__file__), "fixtures", "iegm-franca-2014-2024.csv")


def _limpa(db):
    db.rollback()
    db.execute(delete(FaixaIegm))
    db.commit()


def test_iegm_importa_e_painel(client, tokens, db):
    _limpa(db)
    try:
        corpo = open(ARQ, "rb").read()
        assert client.post("/api/iegm/arquivo", content=corpo, headers=tokens["leitura"]).status_code == 403
        r = client.post("/api/iegm/arquivo", content=corpo, headers={**tokens["admin"], "Content-Type": "text/csv"})
        assert r.status_code == 200, r.text
        assert r.json()["mensagem"] == "IEG-M carregado: anos base 2014 a 2024 (487 linhas)."
        # estado: cada ano e índice soma os 644 municípios
        somas = set(db.execute(select(func.sum(FaixaIegm.municipios)).where(FaixaIegm.abrangencia == "SP")
                               .group_by(FaixaIegm.ano_base, FaixaIegm.indice)).scalars())
        assert somas == {644}
        d = client.get("/api/iegm/painel", headers=tokens["leitura"]).json()
        assert d["anos"] == list(range(2014, 2025)) and d["ultimo"] == 2024 and d["apuracao"]["2024"] == 2025
        ind = {i["codigo"]: i for i in d["indices"]}
        assert list(ind) == list(iegm.INDICES)
        g = ind["IEG-M"]
        assert [s["faixa"] for s in g["serie"]] == ["B", "B", "B", "B", "B", "C+", "C+", "C", "C+", "C+", "C+"]
        assert (g["atual"], g["anterior"], g["tendencia"]) == ("C+", "C+", "igual")
        # 2024: B+ 3, B 97 acima de C+; C+ 261 -> 15,5% em faixa melhor e 40,5% na mesma
        assert (g["pct_melhor"], g["pct_mesma"]) == (15.5, 40.5)
        assert (ind["i-Gov TI"]["atual"], ind["i-Gov TI"]["tendencia"]) == ("B+", "subiu")
        assert (ind["i-Fiscal"]["atual"], ind["i-Fiscal"]["tendencia"]) == ("C+", "caiu")
        assert ind["i-Educ"]["area"] == "Educação"
        # reenviar o mesmo arquivo não duplica (substitui os anos)
        client.post("/api/iegm/arquivo", content=corpo, headers=tokens["admin"])
        assert db.scalar(select(func.count()).select_from(FaixaIegm)) == 487
        e = client.get("/api/iegm/estado", headers=tokens["leitura"]).json()
        assert e["anos"] == list(range(2014, 2025)) and e["ultima_execucao"]["status"] == "sucesso"
    finally:
        _limpa(db)


def test_iegm_arquivo_invalido_nao_grava(client, tokens, db):
    _limpa(db)
    try:
        cab = ";".join(iegm.COLUNAS)
        for corpo, msg in ((b"a;b\n1;2\n", "Cabeçalho esperado"),
                           (f"{cab}\n2024;2025;i-Educ;3516200;D;1\n".encode(), "faixa 'D' inválida"),
                           (f"{cab}\n2024;2025;i-Xyz;3516200;B;1\n".encode(), "índice 'i-Xyz' desconhecido"),
                           (f"{cab}\n2024;2025;i-Educ;Franca;B;1\n".encode(), "código IBGE"),
                           (f"{cab}\n2024;2025;i-Educ;SP;B;10\n2024;2025;i-Educ;SP;B;12\n".encode(), "repete")):
            r = client.post("/api/iegm/arquivo", content=corpo, headers=tokens["admin"])
            assert r.status_code == 422 and msg in r.json()["detail"], (msg, r.text)
        assert db.scalar(select(func.count()).select_from(FaixaIegm)) == 0
        assert client.get("/api/iegm/painel", headers=tokens["leitura"]).json()["indices"] == []
    finally:
        _limpa(db)
