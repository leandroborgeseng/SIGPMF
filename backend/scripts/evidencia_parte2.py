"""Evidência Parte 2 (prata incremental): bronze SINTÉTICA ZZTESTE (pessoas da Saúde + uma tabela grande de fatos,
padrão IPTU.Cadastro, configurável por --tabela, decisão 5 do TL), tempo e RSS de cada fase em subprocesso próprio
(os.wait4), PI5 no volume (incremental x recomposição, contagem + md5) e EXPLAIN (ANALYZE, BUFFERS) antes/depois
(completo e incremental). D6: linhas de homologação (SIL sandbox) entram na bronze e ficam fora da prata (contagem).

Só banco local de teste: o nome precisa conter `_test`. Nenhuma conexão externa, nenhum dado real.

  python scripts/evidencia_parte2.py --db postgresql+psycopg://.../x_test --pessoas 100000 --fatos 6100000 --saida p.json
"""
import argparse
import json
import os
import subprocess
import sys
import time

AQUI = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

GERA_PESSOAS = """
INSERT INTO bronze.registros (sistema_origem, tabela, id_origem, dados, hash, execucao_id, atualizado_origem_em,
                              quarentena, ambiente_origem)
SELECT 'SAUDE', 'pacientes', 'ZZ' || lpad(k::text, 8, '0'),
       jsonb_build_object('Matricula', 'ZZ' || lpad(k::text, 8, '0'), 'Nome', 'ZZTESTE PESSOA ' || (k % 50000)::text,
                          'Dt_Nasc', (date '1950-01-01' + (k % 20000))::text, 'Mae', 'ZZTESTE MAE ' || (k % 900)::text,
                          'Cpf', NULL, 'Cartao_Sus', NULL, 'Sexo', CASE WHEN k % 2 = 0 THEN 'M' ELSE 'F' END),
       md5('p' || k::text), :ex, timestamptz '2026-01-01', CASE WHEN k % 100 = 7 THEN 'chave_nula' END, 'producao'
  FROM generate_series(1, :n) k
"""
# fatos padrão IPTU.Cadastro: um CODI por linha; `v` = versão (rodadas incrementais mudam 1%)
GERA_FATOS = """
INSERT INTO bronze.registros (sistema_origem, tabela, id_origem, dados, hash, execucao_id, atualizado_origem_em,
                              quarentena, ambiente_origem)
SELECT :sis, :tab, lpad(k::text, 14, '0'),
       jsonb_build_object('CODI', lpad(k::text, 14, '0'), 'Insc_Imob', lpad(k::text, 13, '0'), 'NR_CASE', (k % 999)::text,
                          'Cd_Bai', k % 40, 'Quadra', '7', 'Lote', '1', 'STER', 180.0, 'SITU', 1,
                          'Iptu_Terr_Liqu', 100 + :v, 'Iptu_Cons_Liqu', 50.0, 'Data_Alte', '2026-09-01 00:00:00'),
       md5('f' || k::text || ':' || :v), :ex, timestamptz '2026-01-01', NULL, 'producao'
  FROM generate_series(1, :n) k WHERE :v = 0 OR k % 100 = :v
"""
# D5: o resto da fonte IPTU (posses do mesmo CODI) e o Financeiro_Exercicio (medido à parte)
GERA_POSSES = """
INSERT INTO bronze.registros (sistema_origem, tabela, id_origem, dados, hash, execucao_id, quarentena, ambiente_origem)
SELECT 'IPTU', 'Cadastro_Possuidor', lpad(k::text, 14, '0') || '|ZZ' || k,
       jsonb_build_object('CODI', lpad(k::text, 14, '0'), 'CPF_CNPJ', 'ZZ' || k, 'Data_Inclusao', '2020-01-01 00:00:00',
                          'Data_Baixa', NULL, 'Tipo_Posse', 1),
       md5('o' || k), :ex, NULL, 'producao' FROM generate_series(1, :n) k
"""
GERA_FIN = """
INSERT INTO bronze.registros (sistema_origem, tabela, id_origem, dados, hash, execucao_id, quarentena, ambiente_origem)
SELECT 'FINANCEIRO', 'Financeiro_Exercicio', '2026|001|' || k || '|00|0000|0|1|0',
       jsonb_build_object('Exercicio', 2026, 'Tributo', '001', 'Nr_Carne', k, 'Sequencia', '00', 'Referencia', '0000',
                          'Renovacao', 0, 'Parcela', 1, 'Duplicidade', 0, 'Contribuinte', lpad((k % :codis + 1)::text, 14, '0'),
                          'Receita', '001', 'Data_Vencimento', '2026-03-10 00:00:00', 'Valor', 100.0, 'Valor_Pago', 0.0),
       md5('n' || k), :ex, NULL, 'producao' FROM generate_series(1, :n) k
"""
# D6: chaves de homologação (padrão das marcadas pela 0020: SIL sandbox), nunca entram na prata
GERA_SANDBOX = """
INSERT INTO bronze.registros (sistema_origem, tabela, id_origem, dados, hash, execucao_id, quarentena, ambiente_origem)
SELECT 'SIL', 'Empresas', 'ZZSB' || k, jsonb_build_object('CCM', 'ZZSB' || k, 'RazaoSocial', 'ZZTESTE SANDBOX'),
       md5('s' || k), :ex, NULL, 'sandbox' FROM generate_series(1, :n) k
"""

ANTES_COMPLETO = """SELECT r.id FROM bronze.registros r JOIN (SELECT DISTINCT ON (sistema_origem, tabela, id_origem) id
  FROM bronze.registros WHERE quarentena IS NULL AND sistema_origem NOT IN ('GESTAOESCOLAR','CORESSO','IPTU','DIVIDA','FINANCEIRO')
   AND (sistema_origem, tabela) NOT IN (('SAUDE','Consultas'),('SAUDE','Vacina'))
 ORDER BY sistema_origem, tabela, id_origem, id DESC) u ON u.id = r.id ORDER BY r.id"""
DEPOIS_COMPLETO = ANTES_COMPLETO.replace("WHERE quarentena IS NULL AND", "WHERE quarentena IS NULL AND ambiente_origem = 'producao' AND")
# incremental: antes = sem ultima_versao (varrer a bronze da tabela acima da marca e tirar a última por chave);
# depois = bronze.ultima_versao por (sistema, tabela, registro_id > marca)
ANTES_INCR = """SELECT DISTINCT ON (id_origem) id_origem, id FROM bronze.registros WHERE sistema_origem = :sis
  AND tabela = :tab AND quarentena IS NULL AND id > :marca ORDER BY id_origem, id DESC"""
DEPOIS_INCR = """SELECT id_origem, registro_id FROM bronze.ultima_versao WHERE sistema_origem = :sis AND tabela = :tab
  AND registro_id > :marca"""


def _env(db):
    return {**os.environ, "DATABASE_URL": db, "APP_ENV": "test", "JWT_SECRET": "zzteste-evidencia-parte2-000000000",
            "SIGPMF_CHAVE_HASH_SEGREDO": "ZZTESTE-evidencia-0000000000000000000"}


def _fase(a, fase, *extra) -> dict:
    """Roda uma fase num subprocesso e mede tempo de parede e RSS máximo (KiB) dele."""
    t = time.perf_counter()
    p = subprocess.Popen([sys.executable, __file__, "--db", a.db, "--fase", fase, "--tabela", a.tabela, *extra],
                         env=_env(a.db), cwd=AQUI, stdout=subprocess.PIPE)
    out = p.stdout.read().decode()
    _, st, ru = os.wait4(p.pid, 0)
    if st != 0:
        sys.exit(f"fase {fase} falhou")
    r = json.loads(out.strip().splitlines()[-1]) if out.strip() else {}
    return {**r, "s": round(time.perf_counter() - t, 1), "rss_mb": round(ru.ru_maxrss / 1024)}


def fase(a):
    sys.path.insert(0, AQUI)
    from sqlalchemy import text

    from app.core.db import SessionLocal
    from app.services import camadas_incremental as ci
    from app.services import ultima_versao as uv
    from tests.test_zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz_parte2 import snapshot
    sis, tab = a.tabela.split(".", 1)
    tab = tab.split(".")[-1]
    db = SessionLocal()
    out = {}
    if a.fase == "sincroniza":
        out["ate"] = uv.sincroniza(db)
    elif a.fase in ("completa", "incremental", "recompoe"):
        # PI10: prata+tributário medidos à parte do ouro (tempo e RSS de pico até o fim de cada fase)
        import resource

        from app.gold import visao
        from app.silver import processamento as pr
        ate = uv.sincroniza(db)
        modo, _ = ci.decide(db, ate, a.fase == "recompoe")
        out["modo"] = modo
        if modo == "recomposicao":
            t = time.perf_counter()
            pr.reconstruir(db)
            out["prata_s"] = round(time.perf_counter() - t, 1)
            out["rss_prata_mb"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024
            t = time.perf_counter()
            visao.reconstruir(db)
            ci.marca_tudo(db, ate, None)
            db.commit()
            out["ouro_s"] = round(time.perf_counter() - t, 1)
        else:
            r = ci.executa(db, ate)
            out.update({k: r[k] for k in ("prata_s", "ouro_s", "rss_prata_mb", "novas", "alteradas",
                                         "recalculadas", "segundos_por_tipo")})
    elif a.fase == "delta":
        ex = db.execute(text("SELECT max(id) FROM controle.execucoes")).scalar()
        db.execute(text(GERA_FATOS), {"sis": sis, "tab": tab, "n": a.fatos, "v": a.v, "ex": ex})
        # D5: 1% das posses e do Financeiro também muda (dado novo só em campo sintético)
        db.execute(text("""INSERT INTO bronze.registros (sistema_origem, tabela, id_origem, dados, hash, execucao_id,
                                quarentena, ambiente_origem)
            SELECT DISTINCT ON (id_origem) sistema_origem, tabela, id_origem,
                   dados || jsonb_build_object('Data_Baixa', NULL, 'Valor_Pago', :v), md5(hash || :v), :ex, NULL, 'producao'
              FROM bronze.registros WHERE tabela IN ('Cadastro_Possuidor', 'Financeiro_Exercicio')
               AND quarentena IS NULL AND abs(hashtext(id_origem)) % 100 = :v ORDER BY id_origem, id DESC"""),
                   {"v": a.v, "ex": ex})
        db.commit()
    elif a.fase == "snapshot":
        out["snap"] = snapshot(db)
    elif a.fase == "explain":
        marca = db.execute(text("SELECT expandido_ate FROM controle.camadas_marca WHERE sistema = :s AND tabela = :t"),
                           {"s": sis, "t": tab}).scalar() or 0
        # o delta de UMA rodada (1% da tabela): a marca de antes da última rodada, para o plano ter linhas
        marca = max(0, marca - max(a.fatos // 100, 1))
        p = {"sis": sis, "tab": tab, "marca": marca}
        ex = "EXPLAIN (ANALYZE, BUFFERS) "
        db.execute(text("ANALYZE bronze.registros; ANALYZE bronze.ultima_versao"))
        db.commit()
        db.execute(text("DROP INDEX IF EXISTS bronze.ix_bronze_registros_ultima"))  # ANTES: só o índice da 0036
        out["completo_antes"] = [r[0] for r in db.execute(text(ex + ANTES_COMPLETO))]
        out["incremental_antes"] = [r[0] for r in db.execute(text(ex + ANTES_INCR), p)]
        db.rollback()  # o DROP volta: DEPOIS com o índice da 0038
        out["completo_depois"] = [r[0] for r in db.execute(text(ex + DEPOIS_COMPLETO))]
        out["incremental_depois"] = [r[0] for r in db.execute(text(ex + DEPOIS_INCR), p)]
        out["marca"] = marca
        out["sandbox_chaves"] = db.execute(text(
            "SELECT count(DISTINCT (sistema_origem, tabela, id_origem)) FROM bronze.registros "
            "WHERE ambiente_origem <> 'producao'")).scalar()
        out["sandbox_na_prata"] = db.execute(text(
            "SELECT count(*) FROM silver.vinculos v JOIN bronze.registros r ON r.sistema_origem = v.sistema "
            "AND r.tabela = v.tabela AND r.id_origem = v.chave_origem WHERE r.ambiente_origem <> 'producao'")).scalar()
    db.close()
    _ = ci
    print(json.dumps(out, default=str))


def principal(a):
    if "_test" not in a.db.rsplit("/", 1)[-1]:
        sys.exit("recusado: o banco precisa ter _test no nome")
    sys.path.insert(0, AQUI)
    os.environ.update(_env(a.db))
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, text
    eng = create_engine(a.db)
    with eng.begin() as c:
        for s in ("bronze", "silver", "gold", "controle", "ref"):
            c.execute(text(f"DROP SCHEMA IF EXISTS {s} CASCADE"))
    cfg = Config(os.path.join(AQUI, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(AQUI, "alembic"))
    cfg.set_main_option("sqlalchemy.url", a.db)
    t = time.perf_counter()
    command.upgrade(cfg, "head")
    res = {"pessoas": a.pessoas, "fatos": a.fatos, "tabela": a.tabela, "migracao_s": round(time.perf_counter() - t, 1)}
    sis, tab = a.tabela.split(".", 1)
    tab = tab.split(".")[-1]
    with eng.begin() as c:
        ex = c.execute(text("INSERT INTO controle.execucoes (tipo, sistema, status, usuario) "
                            "VALUES ('zzteste_parte2','SAUDE','sucesso','zzteste') RETURNING id")).scalar()
        t = time.perf_counter()
        c.execute(text(GERA_PESSOAS), {"ex": ex, "n": a.pessoas})
        c.execute(text(GERA_FATOS), {"sis": sis, "tab": tab, "n": a.fatos, "v": 0, "ex": ex})
        c.execute(text(GERA_SANDBOX), {"ex": ex, "n": a.sandbox})
        if a.posses:
            c.execute(text(GERA_POSSES), {"ex": ex, "n": a.posses})
        if a.financeiro:
            c.execute(text(GERA_FIN), {"ex": ex, "n": a.financeiro, "codis": max(a.fatos, 1)})
        res["por_tabela"] = {f"{s_}.{t_}": n for s_, t_, n in c.execute(text(
            "SELECT sistema_origem, tabela, count(*) FROM bronze.registros GROUP BY 1, 2 ORDER BY 1, 2"))}
        res["gera_bronze_s"] = round(time.perf_counter() - t, 1)
        res["bronze_linhas"] = c.execute(text("SELECT count(*) FROM bronze.registros")).scalar()
    eng.dispose()
    res["preenche_ultima_versao"] = _fase(a, "sincroniza")
    res["completa"] = _fase(a, "completa")
    for v in range(1, a.rodadas + 1):
        _fase(a, "delta", "--fatos", str(a.fatos), "--v", str(v))
        _fase(a, "sincroniza")
        res[f"incremental_{v}"] = _fase(a, "incremental")
    res["explain"] = _fase(a, "explain", "--fatos", str(a.fatos))
    inc = _fase(a, "snapshot")["snap"]
    res["recomposicao"] = _fase(a, "recompoe")
    rec = _fase(a, "snapshot")["snap"]
    res["pi5_identico"] = inc == rec
    res["pi5_diferencas"] = sorted(k for k in set(inc) | set(rec) if inc.get(k) != rec.get(k))
    res["pi5_contagens"] = {k: v[0] for k, v in rec.items() if v[0]}
    with open(a.saida, "w") as f:
        json.dump(res, f, indent=1, ensure_ascii=False)
    print(json.dumps({k: v for k, v in res.items() if k not in ("explain", "pi5_contagens")}, ensure_ascii=False))


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--db", required=True)
    p.add_argument("--pessoas", type=int, default=100_000)
    p.add_argument("--fatos", type=int, default=1_000_000)
    p.add_argument("--sandbox", type=int, default=500)
    p.add_argument("--tabela", default="IPTU.dbo.Cadastro", help="sistema.[schema.]tabela da fonte grande (PI10)")
    p.add_argument("--rodadas", type=int, default=2)
    p.add_argument("--posses", type=int, default=0, help="linhas de IPTU.Cadastro_Possuidor (D5: fonte IPTU inteira)")
    p.add_argument("--financeiro", type=int, default=0, help="linhas de FINANCEIRO.Financeiro_Exercicio (D5: à parte)")
    p.add_argument("--v", type=int, default=1)
    p.add_argument("--fase")
    p.add_argument("--saida", default="evidencia_parte2.json")
    a = p.parse_args()
    fase(a) if a.fase else principal(a)
