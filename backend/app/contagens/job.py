"""J1–J4: job de contagens automáticas, só leitura e só agregados.

- J1: no SQL Server usa SÓ o login de contagens (SQLSERVER_CONTAGENS_USER); sem ele, a execução é recusada
  (`login_contagens_ausente`). Antes das consultas, confere que o login não escreve nem altera estrutura
  (`login_nao_minimo`). Todo SQL passa pelo `sql_guard` (DML/DDL recusados). No Postgres, transação READ ONLY.
- J2: grava só agregados (números e rótulos curtos declarados), com hash do SQL e uma auditoria por execução.
- J3: falha de um banco vira código fixo (`base_inacessivel`, `consulta_falhou`, `resultado_nao_agregado`),
  nunca `str(exc)`, e o job segue para os demais.
- J4: CPFs repetidos só com ocorrências >= limiar (HAVING no SQL de origem + CHECK no banco); leitura só admin_lgpd.
"""
import hashlib
import logging
import os
import re
from collections.abc import Callable
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.contagens import consultas as C
from app.contagens.modelos import ContagemCpfRepetido, ContagemExecucao, ContagemResultado
from app.core.audit import audita
from app.core.config import get_settings
from app.core.sql_guard import ComandoProibido, executa_somente_leitura, garante_somente_leitura

log = logging.getLogger("sigpmf.contagens")
_ROTULO = re.compile(C.ROTULO_OK)
_CPF11 = re.compile(r"^\d{11}$")

# Permissões que o login de contagens NÃO pode ter (qualquer valor > 0 = recusa). Passa pelo sql_guard: usa os
# códigos de sys.database_permissions (IN=INSERT, UP=UPDATE, DL=DELETE, AL=ALTER, CRTB=CREATE TABLE, CL=CONTROL,
# EX=EXECUTE) e a participação em papéis de escrita/DDL/admin.
SQL_CHECA_LOGIN = ("SELECT IS_SRVROLEMEMBER('sysadmin') a, IS_ROLEMEMBER('db_owner') b, IS_ROLEMEMBER('db_datawriter') c, "
                   "IS_ROLEMEMBER('db_ddladmin') d, (SELECT COUNT(*) FROM sys.database_permissions p "
                   "WHERE p.grantee_principal_id = DATABASE_PRINCIPAL_ID() AND p.state IN ('G','W') "
                   "AND p.type IN ('IN','UP','DL','AL','CRTB','CL','EX')) e")


class ResultadoNaoAgregado(ValueError):
    pass


def sql_hash(sql: str) -> str:
    return hashlib.sha256(sql.encode("utf-8")).hexdigest()


def ativas() -> set[str] | None:
    """CONTAGENS_ATIVAS=Q1,Q11,... libera as marcadas `ajustar` depois de o DBA confirmar os nomes. None = padrão."""
    v = os.environ.get("CONTAGENS_ATIVAS")
    return {x.strip() for x in v.split(",") if x.strip()} if v else None


def selecionadas() -> list[C.Consulta]:
    lib = ativas()
    return [q for q in C.CONSULTAS if (q.id in lib if lib is not None else not q.ajustar)]


def valida_agregado(consulta_id: str, linhas: list[dict], numericas, rotulos=()) -> list[dict[str, Any]]:
    """Só números nas colunas numéricas e rótulos curtos nas de rótulo; qualquer coluna extra invalida (J2)."""
    permitidas = set(numericas) | set(rotulos)
    out = []
    for ln in linhas:
        if set(ln) - permitidas:
            raise ResultadoNaoAgregado(consulta_id)
        linha = {}
        for k, v in ln.items():
            if k in rotulos:
                if v is not None and not (isinstance(v, str) and _ROTULO.match(v)):
                    raise ResultadoNaoAgregado(consulta_id)
                linha[k] = v
            else:
                if v is not None and (isinstance(v, bool) or not isinstance(v, int | float | Decimal)):
                    raise ResultadoNaoAgregado(consulta_id)
                linha[k] = None if v is None else (int(v) if float(v).is_integer() else float(v))
        out.append(linha)
    return out


def _conector_sqlserver(base: str):
    """Conexão pymssql com o login de contagens (só leitura). Nunca usa SQLSERVER_USER."""
    import pymssql
    s = get_settings()
    return pymssql.connect(server=s.sqlserver_host, port=int(s.sqlserver_port), user=s.sqlserver_contagens_user,
                           password=s.sqlserver_contagens_password, database=base, login_timeout=int(s.sqlserver_timeout),
                           timeout=int(s.sqlserver_consulta_timeout), autocommit=False)


def _login_minimo(conn) -> bool:
    linha = executa_somente_leitura(conn, SQL_CHECA_LOGIN, limite=1)
    return bool(linha) and not any(int(v or 0) > 0 for v in linha[0].values())


def _pg(db: Session, sql: str) -> list[dict]:
    garante_somente_leitura(sql)
    with db.get_bind().connect() as c:
        tx = c.begin()
        try:
            c.execute(text("SET TRANSACTION READ ONLY"))
            rs = c.execute(text(sql))
            return [dict(r._mapping) for r in rs.fetchmany(10000)]
        finally:
            tx.rollback()


def executar(db: Session, usuario: str | None, *, conectar: Callable[[str], Any] | None = None,
             agora: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> ContagemExecucao:
    s = get_settings()
    limiar, top = int(s.contagens_cpf_repetido_limiar), int(s.contagens_cpf_repetido_top)
    ex = ContagemExecucao(usuario=usuario, status="em_andamento", limiar_cpf=limiar)
    db.add(ex)
    db.commit()
    consultas = selecionadas()
    precisa_sqlserver = any(q.base != "postgres" for q in consultas) or bool(C.colunas_cpf())
    if conectar is None and precisa_sqlserver and not (s.sqlserver_host and s.sqlserver_contagens_user):
        ex.status, ex.codigo, ex.terminado_em = "recusada", "login_contagens_ausente", agora()
        audita(db, usuario, "contagens_execucao", f"contagem:{ex.id}", {"status": ex.status, "codigo": ex.codigo},
               commit=False)
        db.commit()
        return ex
    conectar = conectar or _conector_sqlserver
    conexoes: dict[str, Any] = {}
    falhas_base: dict[str, str] = {}

    def conn_de(base: str):
        if base in falhas_base:
            return None
        if base not in conexoes:
            try:
                c = conectar(base)
                if not _login_minimo(c):
                    falhas_base[base] = "login_nao_minimo"
                    c.close()
                    return None
                conexoes[base] = c
            except Exception as exc:  # noqa: BLE001 — J3: código fixo, sem str(exc)
                log.warning("contagens: base inacessível (%s, %s)", base, type(exc).__name__)
                falhas_base[base] = "base_inacessivel"
                return None
        return conexoes[base]

    hashes, falhas = [], 0

    def grava(consulta: str, base: str, sql: str, status: str, codigo: str | None, resultado) -> None:
        nonlocal falhas
        h = sql_hash(sql)
        hashes.append({"consulta": consulta, "base": base, "sql_hash": h})
        falhas += status != "ok"
        db.add(ContagemResultado(execucao_id=ex.id, consulta=consulta, base=base, status=status, codigo=codigo,
                                 sql_hash=h, resultado=resultado))
        db.commit()

    def roda(consulta: str, base: str, sql: str, numericas, rotulos=()) -> list[dict] | None:
        try:
            garante_somente_leitura(sql)
        except ComandoProibido:
            grava(consulta, base, sql, "falha", "sql_recusado", None)
            return None
        if base == "postgres":
            try:
                linhas = _pg(db, sql)
            except Exception as exc:  # noqa: BLE001
                log.warning("contagens: %s falhou (%s)", consulta, type(exc).__name__)
                db.rollback()
                grava(consulta, base, sql, "falha", "consulta_falhou", None)
                return None
        else:
            c = conn_de(base)
            if c is None:
                grava(consulta, base, sql, "falha", falhas_base[base], None)
                return None
            try:
                linhas = executa_somente_leitura(c, sql, limite=10000)
            except Exception as exc:  # noqa: BLE001
                log.warning("contagens: %s falhou (%s)", consulta, type(exc).__name__)
                grava(consulta, base, sql, "falha", "consulta_falhou", None)
                return None
        try:
            ok = valida_agregado(consulta, linhas, numericas, rotulos)
        except ResultadoNaoAgregado:
            grava(consulta, base, sql, "falha", "resultado_nao_agregado", None)
            return None
        grava(consulta, base, sql, "ok", None, ok)
        return ok

    try:
        for q in consultas:
            roda(q.id, q.base, q.sql, q.numericas, q.rotulos)
        for base, tabela, col in C.colunas_cpf():
            roda(f"CPF-INV:{tabela}"[:40], base, C.sql_pct_cpf_invalido(tabela, col), ("total", "nulos", "invalidos"))
            _cpf_repetidos(db, ex, base, tabela, col, limiar, top, conn_de, grava, falhas_base)
    finally:
        for c in conexoes.values():
            try:
                c.close()
            except Exception:  # noqa: BLE001
                pass
    ex.status = "concluida" if not falhas else "parcial"
    ex.terminado_em = agora()
    audita(db, usuario, "contagens_execucao", f"contagem:{ex.id}",
           {"status": ex.status, "consultas": len(hashes), "falhas": falhas, "limiar_cpf": limiar, "sql": hashes},
           commit=False)
    db.commit()
    return ex


def _cpf_repetidos(db, ex, base, tabela, col, limiar, top, conn_de, grava, falhas_base) -> None:
    sql = C.sql_cpf_repetido(tabela, col, limiar, top)
    nome = f"J4:{tabela}"[:40]
    c = conn_de(base)
    if c is None:
        grava(nome, base, sql, "falha", falhas_base[base], None)
        return
    try:
        linhas = executa_somente_leitura(c, sql, limite=top)
    except Exception as exc:  # noqa: BLE001
        log.warning("contagens: %s falhou (%s)", nome, type(exc).__name__)
        grava(nome, base, sql, "falha", "consulta_falhou", None)
        return
    n = 0
    for ln in linhas:
        cpf, oc = str(ln.get("cpf") or ""), int(ln.get("ocorrencias") or 0)
        if oc < limiar or not _CPF11.match(cpf):  # defesa extra: abaixo do corte nunca é gravado
            continue
        db.add(ContagemCpfRepetido(execucao_id=ex.id, base=base, tabela=tabela, cpf=cpf, ocorrencias=oc, limiar=limiar))
        n += 1
    grava(nome, base, sql, "ok", None, [{"cpfs_acima_do_limiar": n}])
