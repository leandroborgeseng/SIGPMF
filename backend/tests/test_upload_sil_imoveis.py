"""Upload HTTP do CSV fictício da SIL: admin, streaming, teto, temporário e a mesma carga do comando."""
import asyncio
import threading
import time
from pathlib import Path

import pytest
from sqlalchemy import text

from app.models.controle import Execucao
from app.services import carga_sil_imoveis as ic
from app.services import upload_sil_imoveis as up
from scripts.carga_sil_imoveis import main

FIXTURE = Path(__file__).parent / "fixtures" / "sil_imoveis_fisico_amostra.csv"
CORPO = FIXTURE.read_bytes()
TIPO = ic.TIPO


def _csv(linhas: list[str]) -> bytes:
    cab = ";".join(ic.COLUNAS) + ";\n"
    return (cab + "".join(linhas)).encode("latin-1")


def _linha(ident: str, situacao: str = "A") -> str:
    return ";".join(ident if c == "idfisico" else situacao for c in ic.COLUNAS) + ";\n"


def _multipart(conteudo: bytes, nome: str = "imoveis.csv", boundary: bytes = b"BndSil") -> tuple[bytes, str]:
    corpo = (
        b"--" + boundary + b"\r\n"
        + f'Content-Disposition: form-data; name="arquivo"; filename="{nome}"\r\n'.encode()
        + b"Content-Type: text/csv\r\n\r\n"
        + conteudo
        + b"\r\n--" + boundary + b"--\r\n"
    )
    return corpo, f"multipart/form-data; boundary={boundary.decode()}"


async def _pedacos(corpo: bytes, tamanho: int):
    for i in range(0, len(corpo), tamanho):
        yield corpo[i:i + tamanho]


def _limpa(db, ids: list[int]):
    if ids:
        db.execute(text("DELETE FROM controle.logs WHERE execucao_id = ANY(:ids)"), {"ids": ids})
        db.execute(text("DELETE FROM controle.execucoes WHERE id = ANY(:ids)"), {"ids": ids})
    db.execute(text("DELETE FROM bronze.sil_imoveis_fisico WHERE idfisico = ANY(:f)"), {"f": [1442, 1443]})
    db.commit()


def _esperar(client, headers, eid: int) -> dict:
    ultimo = None
    for _ in range(100):
        r = client.get(f"/api/cargas/sil/imoveis/{eid}", headers=headers)
        assert r.status_code == 200, r.text
        ultimo = r.json()
        if ultimo["situacao"] != "em_andamento":
            return ultimo
        time.sleep(0.05)
    raise AssertionError(ultimo)


def _sumiu(tmp: Path):
    for _ in range(50):
        if not list(tmp.glob("sigpmf-sil-*")):
            return
        time.sleep(0.05)
    raise AssertionError(list(tmp.iterdir()))


@pytest.fixture
def area(monkeypatch, tmp_path):
    monkeypatch.setattr(up, "DIRETORIO", tmp_path)
    return tmp_path


def test_stream_em_pedacos_grava_o_csv_e_para_acima_do_teto(tmp_path):
    conteudo = _csv([_linha("1442")])
    corpo, tipo = _multipart(conteudo)
    destino = tmp_path / "ok.csv"
    nome = asyncio.run(up.salvar_de_stream(_pedacos(corpo, 5), tipo, destino, limite=10_000, folga=10_000, content_length=None))
    assert nome == "imoveis.csv" and destino.read_bytes() == conteudo

    grande, tipo_g = _multipart(b"a" * 500)
    vistos = {"n": 0}

    async def curto():
        for i in range(0, len(grande), 20):
            pedaco = grande[i:i + 20]
            vistos["n"] += len(pedaco)
            if vistos["n"] > 400:
                raise AssertionError("leu o arquivo inteiro")
            yield pedaco

    ruim = tmp_path / "ruim.csv"
    with pytest.raises(up.ExcedeuLimite):
        asyncio.run(up.salvar_de_stream(curto(), tipo_g, ruim, limite=50, folga=80, content_length=None))
    assert vistos["n"] <= 400


def test_content_length_acima_do_teto_nao_le_o_corpo(tmp_path):
    lido = {"n": 0}

    async def nada():
        lido["n"] += 1
        yield b"x"

    with pytest.raises(up.ExcedeuLimite):
        asyncio.run(up.salvar_de_stream(
            nada(), "multipart/form-data; boundary=B", tmp_path / "nao.csv", limite=10, folga=0, content_length=50))
    assert lido["n"] == 0


def test_nginx_permite_corpo_de_cerca_de_200mb():
    texto = (Path(__file__).resolve().parents[2] / "frontend" / "nginx.conf").read_text(encoding="utf-8")
    assert "client_max_body_size 210m;" in texto
    assert "proxy_read_timeout 1800s;" in texto
    assert "proxy_send_timeout 1800s;" in texto
    assert up.LIMITE_BYTES == 200 * 1024 * 1024


def test_cli_continua_funcionando(db, tmp_path):
    ruim = tmp_path / "ruim.csv"
    ruim.write_bytes(b"errado;\n")
    ids: list[int] = []
    try:
        assert main(["--arquivo", str(FIXTURE), "--usuario", "zzcli"]) == 0
        assert main(["--arquivo", str(ruim), "--usuario", "zzcli"]) == 2
        assert main(["--arquivo", str(tmp_path / "ausente.csv")]) == 2
        db.expire_all()
        ids = list(db.scalars(text("SELECT id FROM controle.execucoes WHERE tipo=:t AND usuario='zzcli'"), {"t": TIPO}))
        assert len(ids) == 1
        assert db.scalar(text("SELECT count(*) FROM bronze.sil_imoveis_fisico WHERE idfisico IN (1442, 1443)")) == 2
    finally:
        if not ids:
            db.rollback()
            ids = list(db.scalars(text("SELECT id FROM controle.execucoes WHERE tipo=:t AND usuario='zzcli'"), {"t": TIPO}))
        _limpa(db, ids)


@pytest.mark.parametrize("perfil", ["operador", "leitura", "admin_lgpd"])
def test_nao_admin_recebe_403(client, tokens, perfil):
    r = client.post("/api/cargas/sil/imoveis", files={"arquivo": ("imoveis.csv", CORPO, "text/csv")}, headers=tokens[perfil])
    assert r.status_code == 403
    assert client.get("/api/cargas/sil/imoveis/1", headers=tokens[perfil]).status_code == 403


def test_sem_token_401(client):
    assert client.post("/api/cargas/sil/imoveis", files={"arquivo": ("imoveis.csv", b"a", "text/csv")}).status_code == 401


def test_rejeita_extensao_e_cabecalho_sem_carregar(client, tokens, db, area):
    n0 = db.scalar(text("SELECT count(*) FROM controle.execucoes WHERE tipo=:t"), {"t": TIPO})
    txt = client.post("/api/cargas/sil/imoveis", files={"arquivo": ("imoveis.txt", CORPO, "text/plain")}, headers=tokens["admin"])
    assert txt.status_code == 400 and "csv" in txt.json()["detail"].lower()
    cab = ";".join("outra" if c != "idfisico" else c for c in ic.COLUNAS) + ";\n"
    ruim = client.post("/api/cargas/sil/imoveis", files={"arquivo": ("imoveis.csv", (cab + "1;\n").encode(), "text/csv")},
                       headers=tokens["admin"])
    assert ruim.status_code == 400 and "cabeçalho" in ruim.json()["detail"]
    outro = client.post("/api/cargas/sil/imoveis", files={"arquivo": ("notas.csv.txt", CORPO, "text/csv")}, headers=tokens["admin"])
    assert outro.status_code == 400
    db.expire_all()
    assert db.scalar(text("SELECT count(*) FROM controle.execucoes WHERE tipo=:t"), {"t": TIPO}) == n0
    assert list(area.glob("sigpmf-sil-*")) == []


def test_acima_do_limite_413(client, tokens, monkeypatch, area):
    monkeypatch.setattr(up, "LIMITE_BYTES", 64)
    monkeypatch.setattr(up, "FOLGA_BYTES", 0)
    r = client.post("/api/cargas/sil/imoveis", files={"arquivo": ("imoveis.csv", CORPO, "text/csv")}, headers=tokens["admin"])
    assert r.status_code == 413
    assert list(area.glob("sigpmf-sil-*")) == []


def test_admin_carrega_em_background_e_status_traz_o_total(client, tokens, db, area, monkeypatch):
    libera = threading.Event()
    entrou = threading.Event()
    real = ic.carregar

    def lento(*a, **k):
        entrou.set()
        assert libera.wait(5)
        return real(*a, **k)

    monkeypatch.setattr(ic, "carregar", lento)
    ids: list[int] = []
    try:
        r = client.post("/api/cargas/sil/imoveis", files={"arquivo": ("Imoveis.CSV", CORPO, "text/csv")}, headers=tokens["admin"])
        assert r.status_code == 202, r.text
        corpo = r.json()
        assert corpo["situacao"] == "em_andamento" and corpo["total"] == 0
        assert entrou.wait(5)
        andamento = client.get(f"/api/cargas/sil/imoveis/{corpo['id']}", headers=tokens["admin"]).json()
        assert andamento["situacao"] == "em_andamento" and andamento["total"] == 0
        libera.set()
        final = _esperar(client, tokens["admin"], corpo["id"])
        assert final["situacao"] == "concluido" and final["status"] == "sucesso" and final["total"] == 2
        assert final["lidos"] == 2
        db.expire_all()
        ex = db.get(Execucao, corpo["id"])
        assert ex.usuario == "admin@aion.local" and ex.sistema == "SIL" and ex.tipo == TIPO
        assert db.scalar(text("SELECT count(*) FROM bronze.sil_imoveis_fisico WHERE idfisico IN (1442, 1443)")) == 2
        ids.append(ex.id)
        _sumiu(area)
    finally:
        libera.set()
        _limpa(db, ids)


def test_temporario_some_quando_a_carga_falha(client, tokens, db, area):
    corpo = _csv([_linha("xx")])
    ids: list[int] = []
    try:
        r = client.post("/api/cargas/sil/imoveis", files={"arquivo": ("imoveis.csv", corpo, "text/csv")}, headers=tokens["admin"])
        assert r.status_code == 202, r.text
        final = _esperar(client, tokens["admin"], r.json()["id"])
        assert final["situacao"] == "erro" and final["status"] == "falha"
        ids.append(r.json()["id"])
        _sumiu(area)
        db.expire_all()
        assert db.get(Execucao, r.json()["id"]).usuario == "admin@aion.local"
    finally:
        _limpa(db, ids)


def test_status_de_outra_execucao_e_404(client, tokens, db):
    ex = Execucao(tipo="outra", sistema="SIL", status="sucesso", lidos=1)
    db.add(ex)
    db.commit()
    try:
        assert client.get(f"/api/cargas/sil/imoveis/{ex.id}", headers=tokens["admin"]).status_code == 404
        assert client.get("/api/cargas/sil/imoveis/99999999", headers=tokens["admin"]).status_code == 404
    finally:
        db.execute(text("DELETE FROM controle.execucoes WHERE id=:i"), {"i": ex.id})
        db.commit()
