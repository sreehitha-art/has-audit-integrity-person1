"""Tells you which test groups can run on THIS machine and why others cannot.
  python scripts/preflight.py            (optionally set ANCHOR_TEST_DATABASE_URL first)"""
import importlib, os, sys

def has(m):
    try: importlib.import_module(m); return True
    except Exception: return False

req = {"fastapi (HTTP tests)": has("fastapi") and has("httpx"), "sqlalchemy": has("sqlalchemy"),
       "psycopg2": has("psycopg2"), "alembic": has("alembic"), "pytest": has("pytest"),
       "numpy": has("numpy"), "cryptography": has("cryptography"), "matplotlib": has("matplotlib")}
for k, v in req.items(): print(f"{'OK     ' if v else 'MISSING'} {k}")
url = os.environ.get("ANCHOR_TEST_DATABASE_URL")
pg = "not configured (set ANCHOR_TEST_DATABASE_URL to a DISPOSABLE db, e.g. .../anchor_research)"
if url and req["sqlalchemy"] and req["psycopg2"]:
    try:
        from sqlalchemy import create_engine, text
        with create_engine(url).connect() as c: c.execute(text("SELECT 1"))
        pg = "reachable"
    except Exception as exc: pg = f"UNREACHABLE: {type(exc).__name__}: {str(exc)[:120]}"
elif url: pg = "URL set but sqlalchemy/psycopg2 missing"
print(f"PostgreSQL test database: {pg}")
print("\nWill run : core/contract/workload/metrics/SQLite-standin/static-schema tests (stdlib+numpy+cryptography)")
print("HTTP tests (test_api.py)         :", "WILL RUN" if req["fastapi (HTTP tests)"] else "will SKIP (install requirements.txt)")
print("PostgreSQL tests                 :", "WILL RUN" if pg == "reachable" else "will SKIP -> " + pg)
sys.exit(0)
