"""Evidência P1 (desempenho prata/ouro): roda prata+ouro sobre bronze SINTÉTICA ZZTESTE e grava tempo, contagens e
hash de cada tabela de silver/gold num JSON. Roda com o código de qualquer árvore (--app main ou branch) para provar
que o resultado é IDÊNTICO antes e depois.

Só banco local de teste: o nome do banco precisa conter `_test` (recusa qualquer outro). Nenhuma conexão externa.

  python scripts/evidencia_desempenho_p1.py --app <backend> --db postgresql+psycopg://...:5432/x_test --n 1000000 --saida a.json
  python scripts/evidencia_desempenho_p1.py --compara a.json b.json
"""
import argparse
import json
import os
import sys
import time

GERA_SQL = """
INSERT INTO bronze.registros (sistema_origem, tabela, id_origem, dados, hash, execucao_id, atualizado_origem_em,
                              quarentena, ambiente_origem)
SELECT 'SAUDE', 'pacientes', 'ZZ' || lpad(k::text, 8, '0'),
       jsonb_build_object('Matricula', 'ZZ' || lpad(k::text, 8, '0'),
                          'Nome', 'ZZTESTE ' || (ARRAY['ANA','BRUNO','CARLA','DIEGO','ELAINE','FABIO','GISELE','HUGO'])[1 + (h % 8)]
                                  || ' ' || (ARRAY['SILVA','SOUZA','COSTA','LIMA','ALVES','ROCHA','DIAS'])[1 + ((h / 8) % 7)]
                                  || ' ' || ((h / 56) % 400)::text,
                          'Dt_Nasc', CASE WHEN h % 20 = 0 THEN NULL ELSE (date '1950-01-01' + ((h / 3) % 20000))::text END,
                          'Mae', CASE WHEN h % 10 = 0 THEN '' ELSE 'ZZTESTE MAE ' || ((h / 7) % 900)::text END,
                          'Cpf', NULL, 'Cartao_Sus', NULL, 'Sexo', CASE WHEN h % 2 = 0 THEN 'M' ELSE 'F' END,
                          'versao', v),
       md5(k::text || ':' || v::text), :ex, timestamptz '2026-01-01' + v * interval '1 day',
       CASE WHEN v = 0 AND k % 100 = 7 THEN 'chave_nula' END, 'producao'
  FROM (SELECT k, v, abs(hashtext(k::text || ':' || v::text)) AS h
          FROM generate_series(1, :n) k CROSS JOIN generate_series(0, 1) v
         WHERE v = 0 OR k % 20 = 0) s
 ORDER BY v, k
"""

ANTIGO_SQL = """EXPLAIN SELECT id FROM bronze.registros WHERE id IN (SELECT max(id) FROM bronze.registros
 WHERE quarentena IS NULL AND sistema_origem NOT IN ('IPTU','DIVIDA','GESTAOESCOLAR','CORESSO')
   AND concat(sistema_origem, '|', tabela) NOT IN ('SAUDE|atendimentos')
 GROUP BY sistema_origem, tabela, id_origem) ORDER BY id"""
NOVO_SQL = """EXPLAIN SELECT r.id FROM bronze.registros r JOIN (SELECT DISTINCT ON (sistema_origem, tabela, id_origem) id
  FROM bronze.registros WHERE quarentena IS NULL AND sistema_origem NOT IN ('IPTU','DIVIDA','GESTAOESCOLAR','CORESSO')
   AND (sistema_origem, tabela) NOT IN (('SAUDE','atendimentos'))
 ORDER BY sistema_origem, tabela, id_origem, id DESC) u ON u.id = r.id ORDER BY r.id"""


AGORA = {"gold.indicadores"}  # atualizado_em = now() do cálculo, não vem da fonte


def _hashes(c, text):
    out = {}
    tabs = c.execute(text("SELECT table_schema || '.' || table_name FROM information_schema.tables "
                          "WHERE table_schema IN ('silver','gold') AND table_type = 'BASE TABLE' ORDER BY 1")).scalars()
    for t in list(tabs):
        # id serial e carimbos de now() ficam de fora; o resto da linha entra no hash, em ordem estável
        fora = " - 'atualizado_em'" if t in AGORA else ""
        row = c.execute(text(f"""SELECT count(*), coalesce(md5(string_agg(j, E'\\n' ORDER BY j)), '')
            FROM (SELECT (to_jsonb(x) - 'id' - 'criado_em' - 'gerado_em' - 'calculado_em'{fora})::text AS j FROM {t} x) y""")).one()
        out[t] = {"linhas": row[0], "hash": row[1]}
    return out


def roda(a):
    if "_test" not in a.db.rsplit("/", 1)[-1]:
        sys.exit("recusado: o banco precisa ter _test no nome")
    sys.path.insert(0, a.app)
    os.chdir(a.app)
    os.environ.update(DATABASE_URL=a.db, APP_ENV="test", JWT_SECRET="zzteste-evidencia",
                      SIGPMF_CHAVE_HASH_SEGREDO="ZZTESTE-evidencia-0000000000000000000")
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, text
    eng = create_engine(a.db)
    with eng.begin() as c:
        for s in ("bronze", "silver", "gold", "controle", "ref"):
            c.execute(text(f"DROP SCHEMA IF EXISTS {s} CASCADE"))
    cfg = Config(os.path.join(a.app, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(a.app, "alembic"))
    cfg.set_main_option("sqlalchemy.url", a.db)
    command.upgrade(cfg, "head")
    with eng.begin() as c:
        ex = c.execute(text("INSERT INTO controle.execucoes (tipo, sistema, status, usuario) "
                            "VALUES ('zzteste_evidencia','SAUDE','sucesso','zzteste') RETURNING id")).scalar()
        t = time.perf_counter()
        c.execute(text(GERA_SQL), {"ex": ex, "n": a.n})
        t_gera = time.perf_counter() - t
    res = {"app": a.app, "n": a.n, "gera_bronze_s": round(t_gera, 1)}
    with eng.connect() as c:
        c.execute(text("ANALYZE bronze.registros"))
        res["bronze_linhas"] = c.execute(text("SELECT count(*) FROM bronze.registros")).scalar()
        res["indice"] = bool(c.execute(text("SELECT 1 FROM pg_class WHERE relname='ix_registros_ultima_versao'")).first())
        res["explain_antigo"] = [r[0] for r in c.execute(text(ANTIGO_SQL))]
        res["explain_novo"] = [r[0] for r in c.execute(text(NOVO_SQL))]
    from app.core.db import SessionLocal
    from app.gold import visao
    from app.silver import processamento
    db = SessionLocal()
    t = time.perf_counter()
    sres = processamento.reconstruir(db)
    t_prata = time.perf_counter() - t
    t = time.perf_counter()
    visao.reconstruir(db)
    t_ouro = time.perf_counter() - t
    db.commit()
    db.close()
    res.update(prata_s=round(t_prata, 1), ouro_s=round(t_ouro, 1),
               resumo={k: v for k, v in sres.items() if k != "tributario"})
    with eng.connect() as c:
        res["tabelas"] = _hashes(c, text)
    if a.indice:  # tempo de criação do índice 0036 no volume sintético (drop + create concurrently)
        with eng.connect().execution_options(isolation_level="AUTOCOMMIT") as c:
            c.execute(text("DROP INDEX CONCURRENTLY IF EXISTS bronze.ix_registros_ultima_versao"))
            t = time.perf_counter()
            c.execute(text("CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_registros_ultima_versao ON bronze.registros "
                           "(sistema_origem, tabela, id_origem, id DESC) WHERE quarentena IS NULL"))
            res["indice_criacao_s"] = round(time.perf_counter() - t, 2)
            res["explain_analyze_novo_teste"] = [r[0] for r in c.execute(text(NOVO_SQL.replace("EXPLAIN", "EXPLAIN ANALYZE", 1)))]
            res["explain_analyze_antigo_teste"] = [r[0] for r in c.execute(text(ANTIGO_SQL.replace("EXPLAIN", "EXPLAIN ANALYZE", 1)))]
    eng.dispose()
    with open(a.saida, "w") as f:
        json.dump(res, f, indent=1, ensure_ascii=False)
    print(json.dumps({k: res[k] for k in ("n", "bronze_linhas", "prata_s", "ouro_s", "resumo")}, ensure_ascii=False))


def compara(a, b):
    x, y = (json.load(open(p)) for p in (a, b))
    dif = [t for t in sorted(set(x["tabelas"]) | set(y["tabelas"])) if x["tabelas"].get(t) != y["tabelas"].get(t)]
    print(f"tabelas={len(x['tabelas'])} diferentes={dif} resumo_igual={x['resumo'] == y['resumo']}")
    print(f"antes prata={x['prata_s']}s ouro={x['ouro_s']}s | depois prata={y['prata_s']}s ouro={y['ouro_s']}s")
    return 1 if dif or x["resumo"] != y["resumo"] else 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--app")
    p.add_argument("--db")
    p.add_argument("--n", type=int, default=5000)
    p.add_argument("--saida")
    p.add_argument("--indice", action="store_true")
    p.add_argument("--compara", nargs=2)
    a = p.parse_args()
    sys.exit(compara(*a.compara) if a.compara else roda(a))
