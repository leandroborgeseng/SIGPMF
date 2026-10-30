"""Bench (manual, fora do pytest): GET /api/conectores com volume sintético ZZTESTE.
Uso: BENCH_DATABASE_URL=postgresql+psycopg://...<nome>_test python tests/bench_conectores.py [linhas_bronze]"""
import os
import sys
import time

URL = os.environ["BENCH_DATABASE_URL"]
assert URL.split("?")[0].endswith("_test"), "só banco de teste (_test)"
os.environ["DATABASE_URL"] = URL
os.environ.setdefault("JWT_SECRET", "segredo-de-teste-somente-bench-0000")
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("SIGPMF_CHAVE_HASH_SEGREDO", "ZZTESTE-segredo-hmac-somente-bench-0000")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402

N = int(sys.argv[1]) if len(sys.argv) > 1 else 2_000_000
B = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
eng = create_engine(URL)
REUSA = os.environ.get("BENCH_REUSA") == "1"
with eng.begin() as c:
    for s in ([] if REUSA else ("bronze", "silver", "gold", "controle", "ref")):
        c.execute(text(f"DROP SCHEMA IF EXISTS {s} CASCADE"))
cfg = Config(os.path.join(B, "alembic.ini"))
cfg.set_main_option("script_location", os.path.join(B, "alembic"))
cfg.set_main_option("sqlalchemy.url", URL)
command.upgrade(cfg, "head")
with eng.begin() as c:
    if not REUSA:
        c.execute(text("SET session_replication_role = replica"))  # sem FKs: só volume sintético
        c.execute(text("INSERT INTO controle.execucoes (id, tipo, status, usuario, iniciado_em, sistema, lidos, novos, "
                       "atualizados, ignorados) VALUES (900001,'amostra_conector','sucesso','zzteste@aion.local',now(),"
                       "'SAUDE',0,0,0,0)"))
        c.execute(text("INSERT INTO bronze.registros (sistema_origem, tabela, id_origem, dados, hash, execucao_id) "
                       "SELECT (ARRAY['SAUDE','CEMITERIO','SIL'])[1 + g % 3], 'ZZTESTE_T', 'ZZTESTE-' || g, "
                       "jsonb_build_object('nome','ZZTESTE ' || g), md5(g::text), 900001 FROM generate_series(1, :n) g"),
                  {"n": N})
        c.execute(text("INSERT INTO silver.vinculos (cidadao_id, sistema, tabela, chave_origem, bronze_id, metodo, confianca, "
                       "atributos) SELECT 'zz' || (g % 500000), (ARRAY['SAUDE','CEMITERIO','SIL'])[1 + g % 3], 'ZZTESTE_T', "
                       "'ZZTESTE-' || g, g, 'cpf', 1, '{}' FROM generate_series(1, :n) g"), {"n": N})
        c.execute(text("INSERT INTO gold.cidadao_dados (cidadao_id, dominio, campo, valor, sistema, mais_recente) "
                       "SELECT 'zz' || (g % 500000), 'saude', 'zzteste', 'ZZTESTE', (ARRAY['SAUDE','CEMITERIO','SIL'])[1 + g % 3], "
                       "true FROM generate_series(1, :n) g"), {"n": N})
        c.execute(text("ANALYZE"))
eng.dispose()

from app.core.db import SessionLocal  # noqa: E402
from app.core.security import hash_senha  # noqa: E402
from app.main import app  # noqa: E402
from app.models.controle import Usuario  # noqa: E402

db = SessionLocal()
if db.query(Usuario).filter_by(email="admin@aion.local").first() is None:
    db.add(Usuario(email="admin@aion.local", nome="ZZTESTE admin", perfil="admin", senha_hash=hash_senha("Senha-de-teste-123")))
db.commit()
db.close()
with TestClient(app) as cl:
    tok = cl.post("/api/auth/login", json={"usuario": "admin@aion.local", "senha": "Senha-de-teste-123"}).json()
    h = {"Authorization": f"Bearer {tok.get('access_token') or tok.get('token')}"}
    for rota in ("/api/conectores", "/api/conectores", "/api/conectores/saude/completa", "/api/conectores/saude/completa"):
        t = time.perf_counter()
        r = cl.get(rota, headers=h)
        print(f"{rota}: {r.status_code} {time.perf_counter() - t:.3f}s (bronze={N})")
    time.sleep(3)
    for rota in ("/api/conectores", "/api/conectores/saude/completa"):
        t = time.perf_counter()
        r = cl.get(rota, headers=h)
        print(f"{rota} (cache quente): {r.status_code} {time.perf_counter() - t:.3f}s")
