"""Reset SÓ dos dados da SIL no SIGPMF (pedido do Leandro em 29/10/2026: recomeçar a SIL do zero).

NÃO chama a API SIL. Não toca em Saúde, educação, SEI nem nas outras fontes. A auditoria nunca é apagada: o próprio
reset grava uma linha nela (quem, quando, contagens por tabela).

Uso (dentro do container do backend, com o DATABASE_URL do app):
    python -m scripts.reset_sil --usuario "Nome do operador"              # simulação: mostra e desfaz tudo
    python -m scripts.reset_sil --usuario "Nome do operador" --confirmar  # aplica (só com o OK do Leandro)

Opções: --schema-backup NOME (padrão backup_sil_20261029) e --remover-ouro-so-sil (padrão desligado: o cidadão do
ouro cuja ÚNICA fonte é a SIL fica; ligado, sai só ele, nunca quem também vem de outra fonte).

Tudo numa transação: trava e checa (agenda da SIL desligada, nenhuma execução SIL em_andamento) -> conta -> copia as
linhas que vão sair para o schema de backup (só o dono lê) e confere as contagens -> apaga/atualiza -> confere ->
audita -> COMMIT (com --confirmar) ou ROLLBACK (simulação, que também não deixa o schema de backup). Qualquer
divergência: ROLLBACK e saída != 0. Só imprime contagens (nada de dado pessoal nem a URL do banco).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import zlib
from dataclasses import dataclass, field

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

SCHEMA_BACKUP_PADRAO = "backup_sil_20261029"
# Mesma trava das cargas SIL (amostra, amostra_sil e carga completa): crc32("amostra:<SISTEMA>") em app.services.trava,
# carga_completa._chave_lock e amostra_sil.CHAVE_TRAVA. "sil" minúsculo também, por defesa.
CHAVES_TRAVA_SIL = (zlib.crc32(b"amostra:SIL"), zlib.crc32(b"amostra:sil"))
SIL_EXECS = "SELECT id FROM controle.execucoes WHERE lower(sistema) = 'sil'"
# Cidadão do ouro cuja ÚNICA proveniência é a SIL: sistemas = ["SIL"] e nenhum dado do ouro vindo de outra fonte.
OURO_SO_SIL = ("SELECT cs.cidadao_id FROM gold.cidadao_sistemas cs WHERE cs.sistemas = '[\"SIL\"]'::jsonb "
               "AND NOT EXISTS (SELECT 1 FROM gold.cidadao_dados d WHERE d.cidadao_id = cs.cidadao_id "
               "AND upper(d.sistema) <> 'SIL')")


@dataclass(frozen=True)
class Alvo:
    tabela: str  # schema.tabela
    filtro: str  # WHERE das linhas da SIL
    acao: str = "apaga"  # "apaga" | "atualiza"


# Ordem de remoção (filhos antes de controle.execucoes). Esta lista É o escopo do reset.
ALVOS: tuple[Alvo, ...] = (
    Alvo("bronze.registros", "upper(sistema_origem) = 'SIL'"),  # inclui quarentena e linhas 'sandbox'
    Alvo("silver.empresas_sil", "true"),  # prata exclusiva da SIL
    Alvo("silver.vinculos", "upper(sistema) = 'SIL'"),  # a SIL é "fato": esperado 0, defensivo
    Alvo("silver.correlacoes_pendentes", "upper(sistema) = 'SIL'"),
    Alvo("silver.inconsistencias", "upper(sistema) = 'SIL'"),
    Alvo("gold.cidadao_dados", f"upper(sistema) = 'SIL' AND cidadao_id NOT IN ({OURO_SO_SIL})"),
    Alvo("controle.logs", f"execucao_id IN ({SIL_EXECS})"),
    Alvo("controle.checkpoints_carga", "lower(sistema) = 'sil'"),  # producao e o antigo 'sandbox'
    Alvo("controle.camadas_estado", f"execucao_id IN ({SIL_EXECS})", "atualiza"),  # só solta a FK (execucao_id=NULL)
    Alvo("controle.agendamentos", "conector = 'sil'", "atualiza"),  # volta ao estado inicial, ativo=false
    Alvo("controle.execucoes", "lower(sistema) = 'sil'"),
)
# Só com --remover-ouro-so-sil: o cidadão do ouro cuja única fonte é a SIL (e os dados dele no ouro).
ALVOS_OURO: tuple[Alvo, ...] = (
    Alvo("gold.cidadao_dados", f"cidadao_id IN ({OURO_SO_SIL})"),
    Alvo("gold.cidadao_sistemas", f"cidadao_id IN ({OURO_SO_SIL})"),
)
# Sempre: tira "SIL" da lista de sistemas dos cidadãos que ficam no ouro (o só-SIL, sem a flag, fica como está).
SISTEMAS_COM_SIL = Alvo("gold.cidadao_sistemas", f"sistemas ? 'SIL' AND cidadao_id NOT IN ({OURO_SO_SIL})", "atualiza")
ATUALIZA = {
    # Recalcula a lista: o que já havia menos "SIL", mais as fontes dos dados que ficam no ouro (ninguém com dado de
    # outra fonte termina com []). A linha anterior fica no backup (gold__cidadao_sistemas__tira_sil).
    "gold.cidadao_sistemas": ("sistemas = (SELECT COALESCE(jsonb_agg(u.s ORDER BY u.s COLLATE \"C\"), '[]'::jsonb) FROM ("
                              "SELECT x AS s FROM jsonb_array_elements_text(gold.cidadao_sistemas.sistemas) x "
                              "WHERE upper(x) <> 'SIL' UNION SELECT d.sistema FROM gold.cidadao_dados d "
                              "WHERE d.cidadao_id = gold.cidadao_sistemas.cidadao_id AND upper(d.sistema) <> 'SIL') u)"),
    "controle.camadas_estado": "execucao_id = NULL",
    "controle.agendamentos": ("ativo = false, forcar = false, ultima_data = NULL, tentativas = 0, "
                              "proxima_tentativa_em = NULL, ultimo_status = NULL, ultima_mensagem = NULL, "
                              "ultima_execucao_id = NULL, ultimo_inicio_em = NULL, ultimo_fim_em = NULL, "
                              "assinatura_origem = NULL, ultima_completa_em = NULL, atualizado_por = :usuario, "
                              "atualizado_em = now()"),
}
# Contagens que NÃO podem mudar (outras fontes e auditoria; a auditoria só ganha a linha do reset).
CONTROLES = {
    "bronze.registros (outras fontes)": "SELECT count(*) FROM bronze.registros WHERE upper(sistema_origem) <> 'SIL'",
    "controle.execucoes (outras fontes)": "SELECT count(*) FROM controle.execucoes WHERE lower(coalesce(sistema,'')) <> 'sil'",
    "controle.checkpoints_carga (outras)": "SELECT count(*) FROM controle.checkpoints_carga WHERE lower(sistema) <> 'sil'",
    "controle.logs (outras)": f"SELECT count(*) FROM controle.logs WHERE execucao_id IS NULL OR execucao_id NOT IN ({SIL_EXECS})",
    "silver.cidadaos": "SELECT count(*) FROM silver.cidadaos",
    "silver.vinculos (outras fontes)": "SELECT count(*) FROM silver.vinculos WHERE upper(sistema) <> 'SIL'",
    "gold.cidadao_dados (outras fontes)": "SELECT count(*) FROM gold.cidadao_dados WHERE upper(sistema) <> 'SIL'",
    "gold.cidadao_sistemas (com outra fonte)": f"SELECT count(*) FROM gold.cidadao_sistemas WHERE cidadao_id NOT IN ({OURO_SO_SIL})",
    "controle.agendamentos (outros)": "SELECT count(*) FROM controle.agendamentos WHERE conector <> 'sil'",
}


class Recusa(RuntimeError):
    """Pré-condição não atendida: nada foi alterado."""


class Divergencia(RuntimeError):
    """Conferência falhou: ROLLBACK."""


@dataclass
class Relatorio:
    modo: str
    schema_backup: str
    remover_ouro_so_sil: bool
    antes: dict[str, int] = field(default_factory=dict)
    backup: dict[str, int] = field(default_factory=dict)
    depois: dict[str, int] = field(default_factory=dict)
    controles_antes: dict[str, int] = field(default_factory=dict)
    controles_depois: dict[str, int] = field(default_factory=dict)


def alvos(remover_ouro_so_sil: bool) -> list[Alvo]:
    extra = list(ALVOS_OURO) if remover_ouro_so_sil else []
    lst = list(ALVOS)
    i = next(n for n, a in enumerate(lst) if a.tabela == "controle.logs")
    return lst[:i] + extra + [SISTEMAS_COM_SIL] + lst[i:]


def _so_sil(a: Alvo) -> bool:
    return a.filtro == f"cidadao_id IN ({OURO_SO_SIL})"


def chave(a: Alvo) -> str:
    if a is SISTEMAS_COM_SIL:
        return a.tabela + " (tira SIL)"
    return a.tabela + (" (cidadão só SIL)" if _so_sil(a) else "")


def nome_backup(a: Alvo) -> str:
    sufixo = "__so_sil" if _so_sil(a) else "__tira_sil" if a is SISTEMAS_COM_SIL else ""
    return a.tabela.replace(".", "__") + sufixo


def _conta(c: Connection, sql: str) -> int:
    return int(c.execute(text(sql)).scalar() or 0)


def _contagens(c: Connection, lista: list[Alvo]) -> dict[str, int]:
    return {chave(a): _conta(c, f"SELECT count(*) FROM {a.tabela} WHERE {a.filtro}") for a in lista}


def _verifica_schema(c: Connection, schema: str) -> None:
    if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", schema):
        raise Recusa("nome de schema de backup inválido (use [a-z0-9_])")
    if schema in ("bronze", "silver", "gold", "controle", "public"):
        raise Recusa("o schema de backup não pode ser um schema do app")
    tabelas = [r[0] for r in c.execute(text(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = :s"), {"s": schema})]
    for t in tabelas:
        if _conta(c, f'SELECT count(*) FROM "{schema}"."{t}"'):
            raise Recusa(f"schema de backup {schema} já existe e não está vazio: nada foi alterado "
                         "(use --schema-backup com outro nome)")
    if tabelas:
        raise Recusa(f"schema de backup {schema} já tem tabelas (vazias): remova-as ou use outro --schema-backup")


def _pre_condicoes(c: Connection) -> None:
    ag = c.execute(text("SELECT ativo FROM controle.agendamentos WHERE conector = 'sil' FOR UPDATE")).first()
    if ag is not None and ag.ativo:
        raise Recusa("agenda da SIL está ativa (ativo=true): desligue a agenda antes do reset")
    rodando = _conta(c, "SELECT count(*) FROM controle.execucoes WHERE lower(sistema) = 'sil' AND status = 'em_andamento'")
    if rodando:
        raise Recusa(f"há {rodando} execução(ões) da SIL em_andamento: espere terminar ou cancele")


def _backup(c: Connection, schema: str, lista: list[Alvo], antes: dict[str, int]) -> dict[str, int]:
    c.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))
    feito = {}
    for a in lista:
        nb = nome_backup(a)
        c.execute(text(f'CREATE TABLE "{schema}"."{nb}" AS SELECT * FROM {a.tabela} WHERE {a.filtro}'))
        n = _conta(c, f'SELECT count(*) FROM "{schema}"."{nb}"')
        if n != antes[chave(a)]:
            raise Divergencia(f"backup de {a.tabela}: {n} linha(s), esperado {antes[chave(a)]}")
        feito[f"{schema}.{nb}"] = n
    for a in ALVOS_OURO:  # sem a flag: cópia vazia, para o rollback ser sempre o mesmo SQL
        if a not in lista:
            nb = nome_backup(a)
            c.execute(text(f'CREATE TABLE "{schema}"."{nb}" AS SELECT * FROM {a.tabela} WHERE false'))
            feito[f"{schema}.{nb}"] = 0
    _restringe(c, schema)
    return feito


def _restringe(c: Connection, schema: str) -> None:
    """Só o dono (o usuário do app, POSTGRES_USER, que é o admin do banco) lê o backup."""
    dono = c.execute(text("SELECT current_user")).scalar()
    outros = [r[0] for r in c.execute(text(
        "SELECT rolname FROM pg_roles WHERE rolname <> current_user AND rolname NOT LIKE 'pg\\_%'"))]
    c.execute(text(f'REVOKE ALL ON SCHEMA "{schema}" FROM PUBLIC'))
    c.execute(text(f'REVOKE ALL ON ALL TABLES IN SCHEMA "{schema}" FROM PUBLIC'))
    for r in outros:
        q = '"' + r.replace('"', '""') + '"'
        c.execute(text(f'REVOKE ALL ON SCHEMA "{schema}" FROM {q}'))
        c.execute(text(f'REVOKE ALL ON ALL TABLES IN SCHEMA "{schema}" FROM {q}'))
    qd = '"' + str(dono).replace('"', '""') + '"'
    c.execute(text(f'GRANT USAGE ON SCHEMA "{schema}" TO {qd}'))
    c.execute(text(f'GRANT SELECT ON ALL TABLES IN SCHEMA "{schema}" TO {qd}'))


def executar(engine: Engine, usuario: str, confirmar: bool = False, remover_ouro_so_sil: bool = False,
             schema_backup: str = SCHEMA_BACKUP_PADRAO, saida=print) -> Relatorio:
    usuario = (usuario or "").strip()
    if not usuario:
        raise Recusa("informe --usuario (nome do operador) para a auditoria")
    lista = alvos(remover_ouro_so_sil)
    rel = Relatorio("confirmado" if confirmar else "simulacao", schema_backup, remover_ouro_so_sil)
    with engine.connect() as c:
        tx = c.begin()
        try:
            c.execute(text("SET LOCAL lock_timeout = '10s'"))
            for k in CHAVES_TRAVA_SIL:  # R1: nenhuma carga SIL pode começar (nem estar rodando) durante o reset
                if not c.execute(text("SELECT pg_try_advisory_xact_lock(:k)"), {"k": k}).scalar():
                    raise Recusa("há uma carga da SIL segurando a trava (amostra:SIL): espere terminar")
            _pre_condicoes(c)
            _verifica_schema(c, schema_backup)
            # Auditoria: as linhas que já existiam continuam todas lá. Outras sessões podem auditar em paralelo
            # (login, leitura, uma recusa 409), então não vale comparar o total.
            aud_max = int(c.execute(text("SELECT coalesce(max(id), 0) FROM controle.auditoria")).scalar())
            controles = {**CONTROLES, "controle.auditoria (linhas anteriores)":
                         f"SELECT count(*) FROM controle.auditoria WHERE id <= {aud_max}"}
            rel.controles_antes = {k: _conta(c, q) for k, q in controles.items()}
            rel.antes = _contagens(c, lista)
            _imprime(saida, "ANTES", rel.antes)
            rel.backup = _backup(c, schema_backup, lista, rel.antes)
            _imprime(saida, f"BACKUP ({schema_backup}, só o dono lê)", rel.backup)
            for a in lista:
                if a.acao == "apaga":
                    c.execute(text(f"DELETE FROM {a.tabela} WHERE {a.filtro}"))
                else:
                    c.execute(text(f"UPDATE {a.tabela} SET {ATUALIZA[a.tabela]} WHERE {a.filtro}"), {"usuario": usuario})
            rel.depois = _contagens(c, [a for a in lista if a.acao == "apaga"])
            ag = c.execute(text("SELECT ativo, ultima_execucao_id FROM controle.agendamentos WHERE conector = 'sil'")).first()
            rel.depois["controle.agendamentos (sil ativo)"] = int(bool(ag and ag.ativo))
            rel.depois["gold.cidadao_sistemas (tira SIL)"] = _conta(c, f"SELECT count(*) FROM {SISTEMAS_COM_SIL.tabela} "
                                                                       f"WHERE {SISTEMAS_COM_SIL.filtro}")
            rel.depois["controle.camadas_estado"] = _conta(
                c, "SELECT count(*) FROM controle.camadas_estado ce JOIN controle.execucoes e ON e.id = ce.execucao_id "
                   "WHERE lower(e.sistema) = 'sil'")
            _imprime(saida, "DEPOIS", rel.depois)
            sobrou = {k: v for k, v in rel.depois.items() if v}
            if sobrou:
                raise Divergencia(f"sobraram linhas da SIL: {sobrou}")
            rel.controles_depois = {k: _conta(c, q) for k, q in controles.items()}
            mudou = {k for k in controles if rel.controles_antes[k] != rel.controles_depois[k]}
            if mudou:
                raise Divergencia(f"contagens fora do escopo mudaram: {sorted(mudou)}")
            c.execute(text("INSERT INTO controle.auditoria (usuario, acao, alvo, detalhe) "
                           "VALUES (:u, 'reset_sil', 'SIL', CAST(:d AS JSONB))"),
                      {"u": usuario, "d": json.dumps({
                          "modo": rel.modo, "schema_backup": schema_backup, "remover_ouro_so_sil": remover_ouro_so_sil,
                          "antes": rel.antes, "depois": rel.depois, "backup": rel.backup,
                          "outras_fontes": rel.controles_antes, "api_sil_chamada": False})})
            _imprime(saida, "OUTRAS FONTES (iguais antes/depois)", rel.controles_antes)
            if confirmar:
                tx.commit()
                ativo = c.execute(text("SELECT ativo FROM controle.agendamentos WHERE conector = 'sil'")).scalar()
                c.rollback()
                saida(f"COMMIT: reset da SIL aplicado. Agenda da SIL (relida após o commit): ativo={str(bool(ativo)).lower()}.")
            else:
                tx.rollback()
                saida("SIMULAÇÃO: ROLLBACK, nada foi alterado (nem o schema de backup ficou). "
                      "Use --confirmar para aplicar.")
        except BaseException:
            if tx.is_active:
                tx.rollback()
            raise
    return rel


AGENDA_ESTADO = ("ativo", "forcar", "ultima_data", "tentativas", "proxima_tentativa_em", "ultimo_status", "ultima_mensagem",
                 "ultima_execucao_id", "ultimo_inicio_em", "ultimo_fim_em", "assinatura_origem", "ultima_completa_em",
                 "atualizado_por", "atualizado_em")


def sql_rollback(schema: str = SCHEMA_BACKUP_PADRAO, remover_ouro_so_sil: bool = False) -> str:
    """SQL (psql, uma transação) que devolve as linhas do backup: execuções primeiro (FKs), depois o resto, e por
    fim a agenda e a camadas_estado voltam aos valores anteriores. A agenda volta como estava (ativo=false)."""
    if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", schema):
        raise Recusa("nome de schema de backup inválido")
    lista = alvos(True)  # sempre inclui o ouro "só SIL": a cópia fica vazia se a flag não foi usada
    apaga = [a for a in lista if a.acao == "apaga"]
    ordem = [a for a in apaga if a.tabela == "controle.execucoes"] + [a for a in apaga if a.tabela != "controle.execucoes"]
    linhas = ["\\set ON_ERROR_STOP on", "BEGIN;"]
    for a in ordem:
        linhas.append(f'INSERT INTO {a.tabela} SELECT * FROM "{schema}"."{nome_backup(a)}";')
    sets = ", ".join(f"{c} = b.{c}" for c in AGENDA_ESTADO)
    linhas.append(f'UPDATE controle.agendamentos t SET {sets} FROM "{schema}"."controle__agendamentos" b WHERE t.id = b.id;')
    linhas.append(f'UPDATE gold.cidadao_sistemas t SET sistemas = b.sistemas '
                  f'FROM "{schema}"."{nome_backup(SISTEMAS_COM_SIL)}" b WHERE t.cidadao_id = b.cidadao_id;')
    linhas.append(f'UPDATE controle.camadas_estado t SET execucao_id = b.execucao_id '
                  f'FROM "{schema}"."controle__camadas_estado" b WHERE t.id = b.id;')
    linhas.append("INSERT INTO controle.auditoria (usuario, acao, alvo, detalhe) VALUES "
                  f"(:'operador', 'reset_sil_rollback', 'SIL', '{{\"schema_backup\": \"{schema}\"}}'::jsonb);")
    linhas.append("COMMIT;")
    return "\n".join(linhas) + "\n"


def _imprime(saida, titulo: str, d: dict[str, int]) -> None:
    saida(f"== {titulo}")
    for k, v in d.items():
        saida(f"  {k}: {v}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Reset só dos dados da SIL (simulação por padrão).")
    p.add_argument("--usuario", default="", help="nome do operador (vai para a auditoria)")
    p.add_argument("--confirmar", action="store_true", help="aplica (COMMIT). Sem isto: simulação com ROLLBACK")
    p.add_argument("--remover-ouro-so-sil", action="store_true",
                   help="remove do ouro o cidadão cuja ÚNICA fonte é a SIL (padrão: mantém)")
    p.add_argument("--schema-backup", default=SCHEMA_BACKUP_PADRAO)
    p.add_argument("--imprimir-rollback", action="store_true",
                   help="só imprime o SQL de rollback (psql -v operador=...); não conecta no banco. Sempre devolve todo o ouro "
                        "do backup, com ou sem --remover-ouro-so-sil")
    a = p.parse_args(argv)
    if a.imprimir_rollback:
        print(sql_rollback(a.schema_backup, a.remover_ouro_so_sil), end="")
        return 0
    from app.core.db import get_engine
    try:
        executar(get_engine(), a.usuario, a.confirmar, a.remover_ouro_so_sil, a.schema_backup)
    except Recusa as e:
        print(f"RECUSADO (nada alterado): {e}", file=sys.stderr)
        return 2
    except Divergencia as e:
        print(f"ROLLBACK (nada alterado): {e}", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
