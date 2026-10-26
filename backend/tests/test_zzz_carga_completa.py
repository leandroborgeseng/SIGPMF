# Roda por último (zzz): grava bronze SAUDE/CEMITERIO. Só dados sintéticos (fixture saude+cemiterio).
"""Carga completa real por conector (aceite CC1-CC6, RS3/RS4) e recursos do servidor (RS1, RS2, RS5, RS6)."""
import re

import pytest
from sqlalchemy import func, select, text

from app.connectors.sqlserver.connector import ConfigSqlServer, SqlServerConnector
from app.connectors.sqlserver.politicas import TabelaRecusada, politica
from app.core import recursos
from app.core.db import SessionLocal
from app.core.sql_guard import garante_somente_leitura
from app.ingestion.origem_mock import OrigemMock
from app.models.camadas import RegistroBronze, Vinculo
from app.models.controle import Auditoria, CheckpointCarga, Execucao
from app.services import carga_completa as ccomp
from app.services import carga_conector as cc
from tests.test_zz_carga_conector import Leitor

_FROM = re.compile(r"FROM \[dbo\]\.\[(\w+)\]")


class Cursor:
    def __init__(self, origem):
        self.o, self.res = origem, []

    def execute(self, sql, params=None):
        self.o.sqls.append((sql, params))
        tabela = _FROM.search(sql).group(1)
        linhas = self.o.linhas(tabela)
        if "COUNT(*)" in sql:
            self.res = [(len(linhas),)]
            return
        pos, n = params
        if self.o.falha_na_pagina is not None and self.o.paginas == self.o.falha_na_pagina:
            self.o.falha_na_pagina = None
            raise TimeoutError("timeout sintético em srv-sintetico.invalid")
        self.o.paginas += 1
        if self.o.ao_ler:
            self.o.ao_ler(self.o.paginas)
        self.res = linhas[pos:pos + n]

    def fetchmany(self, n):
        return self.res[:n]


class Conn:
    def __init__(self, origem):
        self.o = origem

    def cursor(self, as_dict=False):
        return Cursor(self.o)

    def rollback(self):
        self.o.rollbacks += 1

    def close(self):
        pass


class Origem(SqlServerConnector):
    """Conector REAL (montar_pagina/contar/ler_pagina + sql_guard); só a conexão pymssql é sintética."""
    def __init__(self, sistema):
        super().__init__(ConfigSqlServer(None, 1433, "Sintetico", None, None, 5, 100), nome="sintetico")
        self.sistema, self.mock = sistema, OrigemMock(conjunto="saude+cemiterio")
        self.sqls, self.rollbacks, self.paginas, self.falha_na_pagina, self.ao_ler = [], 0, 0, None, None

    def linhas(self, tabela):
        pol = politica(self.sistema)
        cols = pol.colunas_select(tabela)
        rows = self.mock.ler(self.sistema, tabela, None, cols)
        chave = ccomp.ordem(self.sistema, tabela)
        return sorted(rows, key=lambda r: tuple(str(r.get(c)) for c in chave))

    def _conectar(self):
        return Conn(self)


@pytest.fixture
def origem(monkeypatch):
    """disparar síncrono + origem sintética; lote pequeno para várias páginas; sem pausa entre lotes."""
    o = {"SAUDE": Origem("SAUDE"), "CEMITERIO": Origem("CEMITERIO")}
    monkeypatch.setenv("CARGA_COMPLETA_LOTE", "2")
    monkeypatch.setenv("CARGA_COMPLETA_PAUSA_MS", "0")
    monkeypatch.setenv("RECURSOS_DISCO_CAMINHO", "/caminho/inexistente-sintetico")
    from app.core.config import get_settings
    get_settings.cache_clear()
    monkeypatch.setattr(ccomp, "leitor_padrao", lambda conector, db: o[cc.CONECTORES[conector][0]])

    def sincrono(ex_id, conector, trava):
        ccomp.rodar(ex_id, conector, trava, leitor=o[cc.CONECTORES[conector][0]])
    monkeypatch.setattr(ccomp, "disparar", sincrono)
    yield o
    get_settings.cache_clear()


def _amostra(db, conector):
    sis = cc.CONECTORES[conector][0]
    ex = cc.executar_amostra(db, "admin@aion.local", conector, leitor=Leitor(sis))
    assert ex.status in ("sucesso", "sem_novidades"), ex.mensagem


def _bronze(db, sistema, tabela=None):
    q = select(func.count()).select_from(RegistroBronze).where(RegistroBronze.sistema_origem == sistema)
    if tabela:
        q = q.where(RegistroBronze.tabela == tabela)
    return db.scalar(q)


def _ultima(db, sistema):
    db.expire_all()
    return db.scalar(select(Execucao).where(Execucao.tipo == ccomp.TIPO, Execucao.sistema == sistema)
                     .order_by(Execucao.id.desc()).limit(1))


# ---------- CC1: liberação ----------
def test_cc1_bloqueada_sem_amostra_ok_e_liberada_depois(client, tokens, origem, db):
    db.add(Execucao(tipo=cc.TIPO_AMOSTRA, sistema="CEMITERIO", status="falha", usuario="t"))
    db.commit()
    assert ccomp.motivo_bloqueio(db, "cemiterio") == "Rode uma amostra com sucesso primeiro"
    r = client.post("/api/conectores/cemiterio/completa", headers=tokens["admin"])
    assert r.status_code == 409 and "amostra com sucesso" in r.json()["detail"]
    _amostra(db, "cemiterio")
    assert ccomp.motivo_bloqueio(db, "cemiterio") is None
    s = client.get("/api/conectores/cemiterio/completa", headers=tokens["admin"]).json()
    assert s["liberada"] is True and s["motivo"] is None
    base = next(b for b in client.get("/api/conectores", headers=tokens["admin"]).json()["bases"]
                if b["conector_id"] == "cemiterio")
    assert base["completa_disponivel"] and base["completa_liberada"] and base["completa_motivo"] is None
    assert ccomp.motivo_bloqueio(db, "iss") is not None  # só Saúde e Cemitério nesta entrega


# ---------- CC5: só admin ----------
def test_cc5_so_admin(client, tokens, origem):
    for perfil in ("leitura", "operador", "admin_lgpd"):
        h = tokens[perfil]
        assert client.post("/api/conectores/saude/completa", headers=h).status_code == 403
        assert client.get("/api/conectores/saude/completa/estimativa", headers=h).status_code == 403
        assert client.post("/api/conectores/saude/completa/cancelar", headers=h).status_code == 403
        assert client.get("/api/sistema/recursos", headers=h).status_code == 403
    assert client.post("/api/conectores/saude/completa").status_code == 401
    assert client.post("/api/conectores/naoexiste/completa", headers=tokens["admin"]).status_code == 404


# ---------- CC2: estimativa ----------
def test_cc2_estimativa_pelo_guard(client, tokens, origem, db):
    _amostra(db, "saude")
    r = client.get("/api/conectores/saude/completa/estimativa", headers=tokens["admin"])
    assert r.status_code == 200, r.text
    tabs = cc.tabelas_do_conector("SAUDE")
    assert set(r.json()["tabelas"]) == set(tabs) and r.json()["total"] == sum(len(origem["SAUDE"].linhas(t)) for t in tabs)
    for sql, _ in origem["SAUDE"].sqls:
        assert sql.startswith("SELECT COUNT(*) FROM [dbo].") and garante_somente_leitura(sql)


# ---------- CC3 / CC4 / CC6: carga, guard, lotes, auditoria, card final ----------
def test_cc3_cc4_cc6_carga_completa_em_lotes(client, tokens, origem, db):
    _amostra(db, "saude")
    o = origem["SAUDE"]
    o.sqls.clear()
    r = client.post("/api/conectores/saude/completa", headers=tokens["admin"])
    assert r.status_code == 202, r.text
    ex = _ultima(db, "SAUDE")
    assert ex.status == "sucesso", ex.mensagem
    tabs = cc.tabelas_do_conector("SAUDE")
    origem_total = sum(len(o.linhas(t)) for t in tabs)
    assert ex.total_origem == origem_total and ex.lidos == origem_total
    # guard: só SELECT, colunas da política (sem CID/senha), ORDER BY pela chave, OFFSET/FETCH com lote <= 2
    paginas = [(s, p) for s, p in o.sqls if "OFFSET" in s]
    assert paginas and all(p[1] == 2 for _, p in paginas)
    for sql, _ in o.sqls:
        assert garante_somente_leitura(sql) and "*" not in sql.replace("COUNT(*)", "")
        assert not re.search(r"\[(?:\w*_)?CID\w*\]|senha", sql, re.IGNORECASE)
    for t in tabs:
        sqls_t = [s for s, _ in paginas if f"[dbo].[{t}]" in s]
        chave = ", ".join(f"[{c}]" for c in ccomp.ordem("SAUDE", t))
        assert sqls_t and all(f"ORDER BY {chave} OFFSET %s ROWS FETCH NEXT %s ROWS ONLY" in s for s in sqls_t)
        offs = [p[0] for s, p in paginas if f"[dbo].[{t}]" in s]
        assert offs == list(range(0, 2 * len(offs), 2))  # paginação contínua, sem pular nem repetir
    assert o.rollbacks == len(o.sqls)  # toda consulta termina em ROLLBACK
    # CC6: card final e reconciliação bronze x COUNT(*)
    s = client.get("/api/conectores/saude/completa", headers=tokens["admin"]).json()
    assert s["execucao"]["status"] == "sucesso" and s["execucao"]["lidos"] == origem_total
    assert s["camadas"]["bronze"] >= origem_total and "prata" in s["camadas"] and "pendencias" in s["camadas"]
    assert all(v["ok"] and v["bronze"] >= v["origem"] == len(o.linhas(t)) for t, v in s["reconciliacao"].items())
    # CC4: auditoria com usuário, horário, lidas, gravadas e resultado
    a = db.scalar(select(Auditoria).where(Auditoria.acao == "carga_completa_finalizada",
                                          Auditoria.alvo == f"execucao:{ex.id}"))
    assert a.usuario == "admin@aion.local" and a.criado_em is not None
    assert a.detalhe["resultado"] == "sucesso" and a.detalhe["lidas"] == origem_total and "gravadas" in a.detalhe
    assert db.scalar(select(func.count()).select_from(Auditoria).where(Auditoria.acao == "carga_completa_iniciada")) >= 1


def test_cc3_reexecucao_nao_duplica_bronze_nem_silver(client, tokens, origem, db):
    _amostra(db, "saude")
    assert client.post("/api/conectores/saude/completa", headers=tokens["admin"]).status_code == 202
    b1 = _bronze(db, "SAUDE")
    v1 = db.scalar(select(func.count()).select_from(Vinculo).where(Vinculo.sistema == "SAUDE"))
    assert client.post("/api/conectores/saude/completa", headers=tokens["admin"]).status_code == 202
    ex = _ultima(db, "SAUDE")
    assert ex.status == "sucesso" and ex.novos == 0 and ex.atualizados == 0 and ex.ignorados == ex.lidos > 0
    assert _bronze(db, "SAUDE") == b1
    assert db.scalar(select(func.count()).select_from(Vinculo).where(Vinculo.sistema == "SAUDE")) == v1


def test_cc3_falha_mantem_lotes_e_retoma_do_checkpoint(client, tokens, origem, db):
    _amostra(db, "cemiterio")
    o = origem["CEMITERIO"]
    o.paginas, o.falha_na_pagina = 0, 1  # 1ª página ok, 2ª dá timeout
    assert client.post("/api/conectores/cemiterio/completa", headers=tokens["admin"]).status_code == 202
    ex = _ultima(db, "CEMITERIO")
    assert ex.status == "falha" and "Tentar de novo" in ex.mensagem and "srv-sintetico" not in ex.mensagem
    cp = db.scalar(select(CheckpointCarga).where(CheckpointCarga.sistema == "CEMITERIO", CheckpointCarga.tabela == "Obitos"))
    assert cp.posicao == 2 and not cp.concluida
    o.sqls.clear()
    assert client.post("/api/conectores/cemiterio/completa", headers=tokens["admin"]).status_code == 202
    ex2 = _ultima(db, "CEMITERIO")
    assert ex2.status == "sucesso", ex2.mensagem
    offs = [p[0] for s, p in o.sqls if "OFFSET" in s and "[dbo].[Obitos]" in s]
    assert offs[0] == 2  # retomou do checkpoint, não do zero
    assert _bronze(db, "CEMITERIO", "Obitos") == len(o.linhas("Obitos")) or ccomp.reconciliacao(db, "CEMITERIO")["Obitos"]["ok"]
    assert all(v["ok"] for v in ccomp.reconciliacao(db, "CEMITERIO").values())


def test_cc3_cancelar_para_sem_apagar(client, tokens, origem, db):
    _amostra(db, "saude")
    o = origem["SAUDE"]

    def pede_cancelamento(n):
        if n == 1:
            s = SessionLocal()
            ccomp.cancelar(s, "saude")
            s.close()
    o.ao_ler = pede_cancelamento
    o.paginas = 0
    antes = _bronze(db, "SAUDE")
    assert client.post("/api/conectores/saude/completa", headers=tokens["admin"]).status_code == 202
    o.ao_ler = None
    ex = _ultima(db, "SAUDE")
    assert ex.status == "cancelada" and "Nada foi apagado" in ex.mensagem
    assert _bronze(db, "SAUDE") >= antes  # nada apagado
    cps = list(db.scalars(select(CheckpointCarga).where(CheckpointCarga.sistema == "SAUDE")))
    assert any(c.posicao > 0 for c in cps) and not all(c.concluida for c in cps)
    assert client.post("/api/conectores/saude/completa/cancelar", headers=tokens["admin"]).status_code == 409
    assert client.post("/api/conectores/saude/completa", headers=tokens["admin"]).status_code == 202  # retoma
    assert _ultima(db, "SAUDE").status == "sucesso"


def test_cc5_trava_409_duas_cargas_do_mesmo_conector(client, tokens, origem, db):
    _amostra(db, "saude")
    outra = db.get_bind().connect()  # simula uma carga viva segurando a trava do conector
    assert outra.execute(text("select pg_try_advisory_lock(:k)"), {"k": ccomp._chave_lock("SAUDE")}).scalar()
    try:
        r = client.post("/api/conectores/saude/completa", headers=tokens["admin"])
        assert r.status_code == 409 and "em andamento" in r.json()["detail"]
        assert client.post("/api/conectores/saude/amostra", headers=tokens["admin"]).status_code == 409
    finally:
        outra.execute(text("select pg_advisory_unlock(:k)"), {"k": ccomp._chave_lock("SAUDE")})
        outra.commit()
        outra.close()
    # registro "em_andamento" sem trava viva (reinício do servidor) não bloqueia para sempre
    db.add(Execucao(tipo=ccomp.TIPO, sistema="SAUDE", status="em_andamento", usuario="t"))
    db.commit()
    assert client.post("/api/conectores/saude/completa", headers=tokens["admin"]).status_code == 202
    assert db.scalar(select(func.count()).select_from(Execucao).where(
        Execucao.tipo == ccomp.TIPO, Execucao.sistema == "SAUDE", Execucao.status == "em_andamento")) == 0


# ---------- RS3 / RS4: trava de disco ----------
def test_rs3_disco_acima_do_limite_recusa_inicio_mas_amostra_segue(client, tokens, origem, db, monkeypatch):
    _amostra(db, "saude")
    monkeypatch.setattr(recursos, "disco", lambda: recursos.Uso(total=100, livre=12))  # 88%
    r = client.post("/api/conectores/saude/completa", headers=tokens["admin"])
    assert r.status_code == 409 and "88%" in r.json()["detail"] and "limite 85%" in r.json()["detail"]
    assert client.get("/api/conectores/saude/completa", headers=tokens["admin"]).json()["liberada"] is False
    assert client.post("/api/conectores/saude/amostra", headers=tokens["admin"]).status_code == 200
    monkeypatch.setenv("RECURSOS_LIMITE_DISCO", "95")  # limite por env
    assert recursos.motivo_disco_cheio() is None


def test_rs4_disco_acima_de_90_pausa_sem_apagar_e_retoma(client, tokens, origem, db, monkeypatch):
    _amostra(db, "saude")
    uso = {"livre": 30}
    monkeypatch.setattr(recursos, "disco", lambda: recursos.Uso(total=100, livre=uso["livre"]))
    o = origem["SAUDE"]
    o.paginas = 0
    o.ao_ler = lambda n: uso.update(livre=5) if n == 1 else None  # durante a carga: 95%
    antes = _bronze(db, "SAUDE")
    assert client.post("/api/conectores/saude/completa", headers=tokens["admin"]).status_code == 202
    o.ao_ler = None
    ex = _ultima(db, "SAUDE")
    assert ex.status == "pausada" and "95%" in ex.pausa_motivo and "limite 90%" in ex.pausa_motivo
    assert _bronze(db, "SAUDE") >= antes
    s = client.get("/api/conectores/saude/completa", headers=tokens["admin"]).json()
    assert s["execucao"]["pausa_motivo"] == ex.pausa_motivo
    uso["livre"] = 40
    assert client.post("/api/conectores/saude/completa", headers=tokens["admin"]).status_code == 202
    assert _ultima(db, "SAUDE").status == "sucesso"


# ---------- Conector: paginação só pela política ----------
def test_pagina_recusa_ordem_fora_da_politica_e_lote_invalido():
    c = SqlServerConnector(ConfigSqlServer(None, 1433, "x", None, None, 5, 100))
    pol = politica("SAUDE")
    sql = c.montar_pagina(pol, "pacientes", ("Matricula",), 500)
    assert sql.startswith("SELECT [Matricula], [Nome]") and "TOP" not in sql and garante_somente_leitura(sql)
    with pytest.raises(TabelaRecusada):
        c.montar_pagina(pol, "pacientes", ("Hipertenso",), 10)
    with pytest.raises(TabelaRecusada):
        c.montar_pagina(pol, "UsuariosWeb", ("Id",), 10)
    with pytest.raises(ValueError):
        c.montar_pagina(pol, "pacientes", ("Matricula",), True)


def test_lote_da_carga_completa_tem_teto(monkeypatch):
    from pydantic import ValidationError

    from app.core.config import Settings
    monkeypatch.setenv("CARGA_COMPLETA_LOTE", "50000")
    with pytest.raises(ValidationError):
        Settings()


# ---------- Recursos do servidor ----------
def test_rs5_disco_sem_leitura_e_memoria(tmp_path, monkeypatch):
    monkeypatch.setenv("RECURSOS_DISCO_CAMINHO", str(tmp_path / "nao-existe"))
    assert recursos.disco() is None and recursos.motivo_disco_cheio() is None
    monkeypatch.setenv("RECURSOS_DISCO_CAMINHO", str(tmp_path))
    d = recursos.disco()
    assert d is not None and d.total > 0
    (tmp_path / "proc").mkdir()
    (tmp_path / "proc/meminfo").write_text("MemTotal:  8000000 kB\nMemFree: 100 kB\nMemAvailable:  2000000 kB\n")
    cg = tmp_path / "sys/fs/cgroup"
    cg.mkdir(parents=True)
    (cg / "memory.max").write_text("max\n")  # Coolify sem limite: usa /proc/meminfo
    (cg / "memory.current").write_text("123\n")
    m = recursos.memoria(tmp_path)
    assert m.total == 8000000 * 1024 and m.livre == 2000000 * 1024 and m.usado_pct == 75.0
    (cg / "memory.max").write_text(str(4 * 1024 ** 3))  # com limite do cgroup
    (cg / "memory.current").write_text(str(1 * 1024 ** 3))
    m = recursos.memoria(tmp_path)
    assert m.total == 4 * 1024 ** 3 and m.livre == 2000000 * 1024  # nunca mais livre que o MemAvailable
    assert recursos.memoria(tmp_path / "vazio") is None


def test_rs2_faixas_por_env(monkeypatch):
    assert [recursos.status_pct(p) for p in (69.9, 70, 85, 85.1, None)] == ["ok", "atencao", "atencao", "critico",
                                                                             "sem_leitura"]
    monkeypatch.setenv("RECURSOS_AMARELO", "50")
    monkeypatch.setenv("RECURSOS_VERMELHO", "60")
    assert recursos.status_pct(55) == "atencao" and recursos.status_pct(61) == "critico"
    monkeypatch.setenv("RECURSOS_AMARELO", "90")  # faixa incoerente: volta ao padrão
    assert recursos.faixas() == (70, 85)


def test_rs1_rs5_rota_recursos_sem_caminho_nem_host(client, tokens, monkeypatch, tmp_path):
    monkeypatch.setenv("RECURSOS_DISCO_CAMINHO", str(tmp_path / "nao-existe"))
    r = client.get("/api/sistema/recursos", headers=tokens["admin"])
    assert r.status_code == 200
    j = r.json()
    assert j["disco"] is None  # sem leitura, nunca 0%
    assert set(j["banco_mb"]) == {"bronze", "silver", "gold", "controle"} and j["banco_mb"]["bronze"] > 0
    assert j["lido_em"] and j["faixas"] == {"amarelo": 70, "vermelho": 85}
    monkeypatch.setenv("RECURSOS_DISCO_CAMINHO", str(tmp_path))
    j = client.get("/api/sistema/recursos", headers=tokens["admin"]).json()
    assert j["disco"]["total_gb"] > 0 and j["disco"]["status"] in ("ok", "atencao", "critico")
    corpo = r.text + str(j)
    assert str(tmp_path) not in corpo and "srvdisk" not in corpo and "/" not in str(j["disco"])
    import socket
    assert socket.gethostname() not in corpo
