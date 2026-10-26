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


def _k(valores):
    """Ordenação da origem sintética: NULL primeiro (como o SQL Server), depois o valor."""
    return tuple((0, "") if v is None else (1, str(v)) for v in valores)


class Cursor:
    def __init__(self, origem):
        self.o, self.res = origem, []

    def execute(self, sql, params=None):
        self.o.sqls.append((sql, params))
        tabela = _FROM.search(sql).group(1)
        linhas = self.o.linhas(tabela)
        chave = ccomp.ordem(self.o.sistema, tabela)
        nula = [r for r in linhas if any(r.get(c) is None for c in chave)]
        if "IS NOT NULL" in sql and "SUM(CASE" not in sql:  # páginas e unicidade: só chave completa (CC9)
            linhas = [r for r in linhas if r not in nula]
        elif "IS NULL" in sql and "TOP (" in sql:  # CC9: SELECT separado e limitado das chaves nulas
            self.o.nulos_lidos += 1
            self.res = nula[:int(re.search(r"TOP \((\d+)\)", sql).group(1))]
            return
        if "SELECT DISTINCT" in sql:  # combinações distintas da chave
            self.res = [(len({tuple(r.get(c) for c in chave) for r in linhas}),)]
            return
        if "SUM(CASE WHEN" in sql:  # total + linhas com NULL na chave
            self.res = [(len(linhas), sum(1 for r in linhas if any(r.get(c) is None for c in chave)))]
            return
        if "COUNT(*)" in sql:
            self.res = [(len(linhas),)]
            return
        if self.o.falha_na_pagina is not None and self.o.paginas == self.o.falha_na_pagina:
            self.o.falha_na_pagina = None
            raise TimeoutError("timeout sintético em srv-sintetico.invalid")
        self.o.paginas += 1
        if self.o.ao_ler:
            self.o.ao_ler(self.o.paginas)
        n = int(re.search(r"TOP \((\d+)\)", sql).group(1))
        ult = self.o.ultima
        if ult is not None:
            linhas = [r for r in linhas if _k(tuple(r.get(c) for c in chave)) > _k(ult)]
        self.res = linhas[:n]

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
        self.ultima, self.extras, self.nulos_lidos = None, {}, 0

    def linhas(self, tabela):
        pol = politica(self.sistema)
        cols = pol.colunas_select(tabela)
        rows = self.mock.ler(self.sistema, tabela, None, cols) + [dict(r) for r in self.extras.get(tabela, [])]
        chave = ccomp.ordem(self.sistema, tabela)
        return sorted(rows, key=lambda r: _k(tuple(r.get(c) for c in chave)))

    def ler_pagina(self, conn, tabela, politica, ordem, ultima, limite):
        self.ultima = ultima  # a origem sintética aplica o mesmo keyset que o SQL montado pelo conector
        return super().ler_pagina(conn, tabela, politica, ordem, ultima, limite)

    def _conectar(self):
        return Conn(self)


@pytest.fixture
def origem(monkeypatch):
    """disparar síncrono + origem sintética; lote pequeno para várias páginas; sem pausa entre lotes."""
    o = {"SAUDE": Origem("SAUDE"), "CEMITERIO": Origem("CEMITERIO")}
    monkeypatch.setenv("CARGA_COMPLETA_LOTE", "2")
    monkeypatch.setenv("CARGA_COMPLETA_PAUSA_MS", "0")
    monkeypatch.setattr(recursos, "disco", lambda: recursos.Uso(total=100, livre=50))  # 50%: liberado
    from app.core.config import get_settings
    get_settings.cache_clear()
    monkeypatch.setattr(ccomp, "leitor_padrao", lambda conector, db: o[cc.CONECTORES[conector][0]])

    def sincrono(ex_id, conector, trava, somente_camadas=False):
        ccomp.rodar(ex_id, conector, trava, leitor=o[cc.CONECTORES[conector][0]], somente_camadas=somente_camadas)
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
        assert client.get("/api/conectores/saude/completa", headers=h).status_code == 403
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
        assert sql.startswith("SELECT COUNT(*)") and "FROM [dbo]." in sql and garante_somente_leitura(sql)


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
    paginas = [(s, p) for s, p in o.sqls if "TOP (" in s]
    assert paginas and all("TOP (2)" in s for s, _ in paginas)
    for sql, _ in o.sqls:
        assert garante_somente_leitura(sql) and "*" not in sql.replace("COUNT(*)", "")
        assert not re.search(r"\[(?:\w*_)?CID\w*\]|senha", sql, re.IGNORECASE)
        assert "OFFSET" not in sql
    for t in tabs:
        sqls_t = [(s, p) for s, p in paginas if f"[dbo].[{t}]" in s]
        chave = ", ".join(f"[{c}]" for c in ccomp.ordem("SAUDE", t))
        assert sqls_t and all(s.endswith(f"ORDER BY {chave}") for s, _ in sqls_t)
        assert "%s" not in sqls_t[0][0] and not sqls_t[0][1]  # 1ª página sem keyset; todas só com chave completa (CC9)
        assert all("IS NOT NULL" in s for s, _ in sqls_t) and all(" > %s" in s for s, _ in sqls_t[1:])
        assert all("%s" in s and p for s, p in sqls_t[1:])  # sempre parametrizado
    assert o.rollbacks == len(o.sqls)  # toda consulta termina em ROLLBACK
    # CC6: card final e reconciliação bronze x COUNT(*)
    s = client.get("/api/conectores/saude/completa", headers=tokens["admin"]).json()
    assert s["execucao"]["status"] == "sucesso" and s["execucao"]["lidos"] == origem_total
    assert s["camadas"]["bronze"] >= origem_total and "prata" in s["camadas"] and "pendencias" in s["camadas"]
    for t, v in s["reconciliacao"].items():  # ok só quando bronze == origem (o banco de teste pode ter bronze de outros módulos)
        assert v["origem"] == len(o.linhas(t)) and v["ok"] == (v["bronze"] == v["origem"]) and v["bronze"] >= v["origem"]
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
    assert cp.posicao == 2 and not cp.concluida and cp.ultima_chave is not None
    ckpt = list(cp.ultima_chave)
    o.sqls.clear()
    assert client.post("/api/conectores/cemiterio/completa", headers=tokens["admin"]).status_code == 202
    ex2 = _ultima(db, "CEMITERIO")
    assert ex2.status == "sucesso", ex2.mensagem
    primeira = next(p for s, p in o.sqls if "TOP (" in s and "[dbo].[Obitos]" in s)
    assert list(primeira)[-len(ckpt):] == ckpt  # retomou do checkpoint (último termo do keyset = chave salva)
    ids = [r[0] for r in db.execute(select(RegistroBronze.id_origem).where(
        RegistroBronze.sistema_origem == "CEMITERIO", RegistroBronze.tabela == "Obitos"))]
    assert len(ids) == len(set(ids))
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
    sql, _ = c.montar_pagina(pol, "pacientes", ("Matricula",), 500)
    assert sql.startswith("SELECT TOP (500) [Matricula], [Nome]") and "OFFSET" not in sql and garante_somente_leitura(sql)
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
    assert recursos.motivo_disco_cheio(exige_leitura=True) == recursos.SEM_LEITURA_DISCO
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


# ---------- Keyset ----------
def test_keyset_chave_simples_e_composta_parametrizado():
    c = SqlServerConnector(ConfigSqlServer(None, 1433, "x", None, None, 5, 100))
    pol = politica("SAUDE")
    sql, p = c.montar_pagina(pol, "pacientes", ("Matricula",), 500)
    assert sql.endswith("FROM [dbo].[pacientes] WHERE ([Matricula] IS NOT NULL) ORDER BY [Matricula]") and p == () and sql.startswith("SELECT TOP (500) ")
    sql, p = c.montar_pagina(pol, "pacientes", ("Matricula",), 500, ("M9",))
    assert "WHERE (([Matricula] > %s)) AND ([Matricula] IS NOT NULL) ORDER BY [Matricula]" in sql and p == ("M9",) and garante_somente_leitura(sql)
    sql, p = c.montar_pagina(pol, "Pacientes_CNS", ("Matricula", "Cartao_Sus"), 10, ("M1", "700"))
    assert "WHERE (([Matricula] > %s) OR ([Matricula] = %s AND [Cartao_Sus] > %s))" in sql and p == ("M1", "M1", "700")
    w, p = SqlServerConnector.filtro_keyset(("a", "b", "c"), (1, None, 3))
    assert w == " WHERE (([a] > %s) OR ([a] = %s AND [b] IS NOT NULL) OR ([a] = %s AND [b] IS NULL AND [c] > %s))"
    assert p == (1, 1, 1, 3)
    with pytest.raises(ValueError):
        SqlServerConnector.filtro_keyset(("a", "b"), (1,))
    for v in (("x'; DROP TABLE y",), ("1 OR 1=1",)):  # valor nunca vai para o texto do SQL
        sql, p = c.montar_pagina(pol, "pacientes", ("Matricula",), 5, v)
        assert v[0] not in sql and p == v


def test_keyset_codifica_tipos_do_checkpoint():
    from datetime import date, datetime
    from decimal import Decimal
    v = (datetime(2026, 1, 2, 3, 4, 5), date(2026, 1, 2), Decimal("1.50"), b"\x01\xff", None, "A", 7)
    assert ccomp.decodifica_chave(ccomp.codifica_chave(v)) == v
    assert ccomp.decodifica_chave(None) is None


def test_keyset_linhas_novas_durante_a_carga_sem_pular_nem_duplicar(client, tokens, origem, db):
    _amostra(db, "cemiterio")
    o = origem["CEMITERIO"]
    base = o.mock.ler("CEMITERIO", "Obitos", None, politica("CEMITERIO").colunas_select("Obitos"))
    originais = {ccomp.ordem("CEMITERIO", "Obitos") and "|".join("" if r.get(c) is None else str(r.get(c))
                 for c in ccomp.ordem("CEMITERIO", "Obitos")) for r in base}
    novo = lambda cod: {**base[0], "COD_CEMITERIO": cod, "SEQUENCIA": 1}  # noqa: E731

    def entra_linha(n):
        if n == 1:  # durante a carga: uma linha antes do cursor e uma depois
            o.extras["Obitos"] = [novo("0000-ANTES"), novo("zzzz-DEPOIS")]
    o.paginas, o.ao_ler = 0, entra_linha
    assert client.post("/api/conectores/cemiterio/completa", headers=tokens["admin"]).status_code == 202
    o.ao_ler = None
    ex = _ultima(db, "CEMITERIO")
    assert ex.status == "sucesso", ex.mensagem
    ids = [r[0] for r in db.execute(select(RegistroBronze.id_origem).where(
        RegistroBronze.sistema_origem == "CEMITERIO", RegistroBronze.tabela == "Obitos",
        RegistroBronze.execucao_id == ex.id))]
    assert len(ids) == len(set(ids))  # nenhuma duplicada nesta carga
    todos = {r[0] for r in db.execute(select(RegistroBronze.id_origem).where(
        RegistroBronze.sistema_origem == "CEMITERIO", RegistroBronze.tabela == "Obitos"))}
    assert originais <= todos  # nenhuma linha original pulada
    assert any(i.startswith("zzzz-DEPOIS") for i in todos)  # a que entrou depois do cursor também veio
    o.extras.clear()


def test_rs5_sem_leitura_do_disco_bloqueia_a_carga(client, tokens, origem, db, monkeypatch):
    _amostra(db, "saude")
    monkeypatch.setattr(recursos, "disco", lambda: None)
    r = client.post("/api/conectores/saude/completa", headers=tokens["admin"])
    assert r.status_code == 409 and r.json()["detail"] == recursos.SEM_LEITURA_DISCO
    s = client.get("/api/conectores/saude/completa", headers=tokens["admin"]).json()
    assert s["liberada"] is False and s["motivo"] == recursos.SEM_LEITURA_DISCO
    assert client.post("/api/conectores/saude/amostra", headers=tokens["admin"]).status_code == 200  # amostra segue


# ---------- Keyset: chave única e sem NULL; fronteira do lote ----------
def test_keyset_recusa_chave_com_null(client, tokens, origem, db):
    _amostra(db, "cemiterio")
    o = origem["CEMITERIO"]
    base = o.mock.ler("CEMITERIO", "Obitos", None, politica("CEMITERIO").colunas_select("Obitos"))
    o.extras["Obitos"] = [{**base[0], "SEPULTURA": "S-NULA", "GAVETA": None}]  # chave composta com NULL
    try:
        r = client.get("/api/conectores/cemiterio/completa/estimativa", headers=tokens["admin"])
        assert r.status_code == 409 and "Obitos" in r.json()["detail"] and "nula" in r.json()["detail"]
        o.sqls.clear()
        assert client.post("/api/conectores/cemiterio/completa", headers=tokens["admin"]).status_code == 202
        ex = _ultima(db, "CEMITERIO")
        assert ex.status == "falha" and "chave" in ex.mensagem and "nula" in ex.mensagem
        assert not [x for x, _ in o.sqls if "TOP (" in x]  # recusou antes de ler qualquer página
        q = [x for x, _ in o.sqls if "SUM(CASE WHEN" in x]
        assert q and "[GAVETA] IS NULL" in q[0] and all(garante_somente_leitura(x) for x, _ in o.sqls)
    finally:
        o.extras.clear()


def test_keyset_recusa_chave_repetida(client, tokens, origem, db):
    _amostra(db, "saude")
    o = origem["SAUDE"]
    base = o.mock.ler("SAUDE", "pacientes", None, politica("SAUDE").colunas_select("pacientes"))
    o.extras["pacientes"] = [{**base[0], "Nome": "OUTRO NOME SINTETICO"}]  # mesma Matricula
    try:
        r = client.get("/api/conectores/saude/completa/estimativa", headers=tokens["admin"])
        assert r.status_code == 409 and "pacientes" in r.json()["detail"] and "não é única" in r.json()["detail"]
        q = [x for x, _ in o.sqls if "SELECT DISTINCT" in x][-1]
        assert q == ("SELECT COUNT(*) FROM (SELECT DISTINCT [Matricula] FROM [dbo].[pacientes] "
                     "WHERE [Matricula] IS NOT NULL) AS chaves")
    finally:
        o.extras.clear()


def test_keyset_valores_iguais_na_fronteira_do_lote(client, tokens, origem, db):
    """Chave composta (Matricula, Cartao_Sus) com a mesma Matricula atravessando o limite do lote (lote = 2)."""
    _amostra(db, "saude")
    o = origem["SAUDE"]
    base = o.mock.ler("SAUDE", "Pacientes_CNS", None, politica("SAUDE").colunas_select("Pacientes_CNS"))
    o.extras["Pacientes_CNS"] = [{**base[0], "Matricula": 1001, "Cartao_Sus": f"79550515948527{i}"} for i in range(5)]
    try:
        assert client.post("/api/conectores/saude/completa", headers=tokens["admin"]).status_code == 202
        ex = _ultima(db, "SAUDE")
        assert ex.status == "sucesso", ex.mensagem
        esperado = {f"{r['Matricula']}|{r['Cartao_Sus']}" for r in o.linhas("Pacientes_CNS")}
        lidos = {r[0] for r in db.execute(select(RegistroBronze.id_origem).where(
            RegistroBronze.sistema_origem == "SAUDE", RegistroBronze.tabela == "Pacientes_CNS"))}
        assert esperado <= lidos
        ps = [p for x, p in o.sqls if "TOP (" in x and "[dbo].[Pacientes_CNS]" in x and p]
        assert any(p[0] == p[1] == 1001 for p in ps)  # houve página cortada no meio da Matricula 1001
        rec = ccomp.reconciliacao(db, "SAUDE")["Pacientes_CNS"]
        assert rec["origem"] == len(esperado) and rec["ok"] == (rec["bronze"] == rec["origem"])
    finally:
        o.extras.clear()


def test_reconciliacao_bronze_acima_da_origem_e_divergencia(db):
    cp = db.scalar(select(CheckpointCarga).where(CheckpointCarga.sistema == "SAUDE", CheckpointCarga.tabela == "pacientes"))
    assert cp is not None
    b = db.scalar(select(func.count(func.distinct(RegistroBronze.id_origem))).where(
        RegistroBronze.sistema_origem == "SAUDE", RegistroBronze.tabela == "pacientes"))
    cp.total_origem = b - 1  # origem perdeu uma linha: bronze acima
    db.commit()
    r = ccomp.reconciliacao(db, "SAUDE")["pacientes"]
    assert r["bronze"] > r["origem"] and r["ok"] is False


def test_rs5_sem_leitura_do_disco_durante_a_carga_pausa(client, tokens, origem, db, monkeypatch):
    _amostra(db, "saude")
    leitura = {"ok": True}
    monkeypatch.setattr(recursos, "disco", lambda: recursos.Uso(total=100, livre=50) if leitura["ok"] else None)
    o = origem["SAUDE"]
    o.paginas, o.ao_ler = 0, (lambda n: leitura.update(ok=False) if n == 1 else None)
    antes = _bronze(db, "SAUDE")
    assert client.post("/api/conectores/saude/completa", headers=tokens["admin"]).status_code == 202
    o.ao_ler = None
    ex = _ultima(db, "SAUDE")
    assert ex.status == "pausada" and ex.pausa_motivo == recursos.SEM_LEITURA_DISCO
    assert _bronze(db, "SAUDE") >= antes  # nada apagado
    leitura["ok"] = True
    assert client.post("/api/conectores/saude/completa", headers=tokens["admin"]).status_code == 202
    assert _ultima(db, "SAUDE").status == "sucesso"


def test_reconciliacao_unitaria_isolada_igual_ok_menor_e_maior_divergem(db):
    """Isolado: sistema sintético próprio (sem bronze de outros módulos), chama reconciliacao() direto."""
    sis = "TESTE_RECONCILIACAO"
    ex = Execucao(tipo=ccomp.TIPO, sistema=sis, status="sucesso", usuario="t")
    db.add(ex)
    db.flush()
    db.add_all([CheckpointCarga(sistema=sis, tabela=t, posicao=n, total_origem=n, concluida=True, execucao_id=ex.id)
                for t, n in (("igual", 3), ("menor", 3), ("maior", 3))])
    linhas = {"igual": 3, "menor": 2, "maior": 4}
    for t, n in linhas.items():
        for i in range(n):
            db.add(RegistroBronze(sistema_origem=sis, tabela=t, id_origem=str(i), dados={"i": i}, hash=f"h{i}",
                                  execucao_id=ex.id))
    # histórico: mesma chave com outro hash conta uma vez só
    db.add(RegistroBronze(sistema_origem=sis, tabela="igual", id_origem="0", dados={"i": 0, "v": 2}, hash="h0v2",
                          execucao_id=ex.id))
    db.commit()
    r = ccomp.reconciliacao(db, sis)
    assert r["igual"] == {"origem": 3, "bronze": 3, "quarentena": 0, "ok": True, "status": "ok"}
    assert r["menor"] == {"origem": 3, "bronze": 2, "quarentena": 0, "ok": False, "status": "divergente"}
    assert r["maior"] == {"origem": 3, "bronze": 4, "quarentena": 0, "ok": False, "status": "divergente"}
