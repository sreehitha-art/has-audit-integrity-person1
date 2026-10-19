"""STATIC consistency check between the three schema definitions: ORM model, Alembic migration, schema.sql.
This does NOT execute the migration (no PostgreSQL/Alembic here); it only catches drift in column names,
nullability and unique constraints between the files. Real validation = `alembic upgrade head` + test_api_postgres."""
import ast, re, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _kw(call, name, default=None):
    for k in call.keywords:
        if k.arg == name and isinstance(k.value, ast.Constant): return k.value.value
    return default


def parse_migration():
    tree = ast.parse((ROOT / "migrations/versions/0001_create_retained_checkpoints.py").read_text())
    cols, uniques = {}, {}
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute):
            if n.func.attr == "Column":
                pk = _kw(n, "primary_key", False)
                cols[n.args[0].value] = (not pk) and _kw(n, "nullable", True) is True
            elif n.func.attr == "UniqueConstraint":
                uniques[_kw(n, "name")] = tuple(a.value for a in n.args)
    return cols, uniques


def parse_model():
    tree = ast.parse((ROOT / "models/db_models.py").read_text()); cols, uniques = {}, {}
    for n in ast.walk(tree):
        if isinstance(n, ast.Assign) and isinstance(n.value, ast.Call) and getattr(n.value.func, "id", "") == "Column":
            pk = _kw(n.value, "primary_key", False)
            cols[n.targets[0].id] = (not pk) and _kw(n.value, "nullable", True) is True
        if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "UniqueConstraint":
            uniques[_kw(n, "name")] = tuple(a.value for a in n.args)
    return cols, uniques


def parse_sql():
    raw = (ROOT / "schema.sql").read_text(); cols, uniques = {}, {}
    sql = "\n".join(l for l in raw.splitlines() if not l.strip().startswith("--"))   # drop comments (they contain parentheses)
    body = sql[sql.index("(") + 1: sql.rindex(")")]
    for line in [l.strip().rstrip(",") for l in body.splitlines() if l.strip()]:
        m = re.match(r"CONSTRAINT (\w+) UNIQUE \(([^)]*)\)", line)
        if m: uniques[m.group(1)] = tuple(c.strip() for c in m.group(2).split(",")); continue
        name = line.split()[0]
        cols[name] = ("NOT NULL" not in line) and ("PRIMARY KEY" not in line)
    return cols, uniques


class SchemaConsistency(unittest.TestCase):
    def test_three_definitions_agree(self):
        mig, model, sql = parse_migration(), parse_model(), parse_sql()
        self.assertEqual(len(mig[0]), 13)
        for label, other in (("model", model), ("schema.sql", sql)):
            self.assertEqual(set(mig[0]), set(other[0]), f"column names differ: migration vs {label}")
            self.assertEqual(mig[0], other[0], f"nullability differs: migration vs {label}")
            self.assertEqual(mig[1], other[1], f"unique constraints differ: migration vs {label}")
        self.assertEqual(mig[1], {"uq_chain_seq": ("chain_id", "checkpoint_sequence"), "uq_idempotency_key": ("idempotency_key",)})

    def test_sqlite_standin_ddl_has_same_columns_and_constraints(self):
        from anchor_service.services.sqlite_repository import COLS, DDL
        self.assertEqual(set(COLS), set(parse_migration()[0]))
        self.assertIn("uq_chain_seq", DDL); self.assertIn("uq_idempotency_key", DDL)

    def test_repository_record_fields_match_columns(self):
        from anchor_service.services.core import Record
        self.assertEqual(set(Record.__dataclass_fields__), set(parse_migration()[0]))

if __name__ == "__main__":
    unittest.main()
