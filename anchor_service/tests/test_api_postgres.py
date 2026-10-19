"""PostgreSQL integration tests: persistence across 'restart', DB-level uniqueness, receipt-after-commit.
Run only when ANCHOR_TEST_DATABASE_URL is set (a DISPOSABLE database, e.g. anchor_research).
  export ANCHOR_TEST_DATABASE_URL=postgresql+psycopg2://user:pw@localhost:5432/anchor_research
  alembic upgrade head   (with ANCHOR_DATABASE_URL pointing at the same DB)
  pytest anchor_service/tests/test_api_postgres.py
"""
import os, unittest
URL = os.environ.get("ANCHOR_TEST_DATABASE_URL")
try:
    import sqlalchemy; HAVE = True
except Exception:
    HAVE = False
from anchor_service.tests.helpers import chain, new_keypair


@unittest.skipUnless(URL and HAVE, "set ANCHOR_TEST_DATABASE_URL and install requirements to run")
class PostgresTests(unittest.TestCase):
    def setUp(self):
        from anchor_service.database import make_engine, make_session_factory
        from anchor_service.services.sql_repository import SqlRepository
        assert "dev" not in URL.rsplit("/", 1)[-1], "refusing to run destructive test on a *_dev database"
        self.engine = make_engine(URL); self.mk = lambda: SqlRepository(make_session_factory(self.engine))
        with self.engine.begin() as c: c.exec_driver_sql("TRUNCATE TABLE retained_checkpoints")
        self.key, self.trusted = new_keypair(); self.cps = chain(self.key, 3)

    def _core(self):
        from anchor_service.services.core import AnchorCore
        return AnchorCore(self.mk(), self.trusted)       # a brand-new core+engine session == 'restart'

    def test_persistence_across_restart_and_retry(self):
        cp, sig, dig = self.cps[0]
        rec, created = self._core().ingest({"checkpoint": cp, "signature": sig, "idempotency_key": "p1"})
        self.assertTrue(created)
        core2 = self._core()                              # simulated restart
        self.assertEqual(core2.repo.get_by_id(rec.anchor_id).checkpoint_digest, dig)
        rec2, created2 = core2.ingest({"checkpoint": cp, "signature": sig, "idempotency_key": "p1"})
        self.assertFalse(created2); self.assertEqual(rec2.receipt(), rec.receipt())
        self.assertEqual(core2.repo.count(), 1)

    def test_db_unique_constraints_enforced(self):
        from anchor_service.services.core import Conflict, Record
        repo = self.mk(); cp, sig, dig = self.cps[0]
        mk = lambda aid, key, seq: Record(aid, "hms-main", seq, 10, "a" * 64, None, dig, "{}", sig, "app-key-1",
                                          "2026-01-01T00:00:00.000000Z", "", key)
        repo.insert(mk("00000000-0000-0000-0000-000000000001", "x", 1))
        with self.assertRaises(Conflict): repo.insert(mk("00000000-0000-0000-0000-000000000002", "y", 1))  # (chain,seq) only
        with self.assertRaises(Conflict): repo.insert(mk("00000000-0000-0000-0000-000000000003", "x", 2))  # idempotency key only
        self.assertEqual(repo.count(), 1)

    def test_receipt_visible_to_independent_session_and_timestamp_order(self):
        rec, _ = self._core().ingest({"checkpoint": self.cps[0][0], "signature": self.cps[0][1], "idempotency_key": "v1"})
        self.assertIsNotNone(self.mk().get_by_id(rec.anchor_id))
        self.assertTrue(rec.received_at <= rec.durably_retained_at)

    def test_multi_worker_race_same_request_one_row_identical_receipts(self):
        import threading
        n = 12; bar = threading.Barrier(n); out = [None] * n
        def worker(i):
            core = self._core(); core._ingest_lock = type("L", (), {"__enter__": lambda s: s, "__exit__": lambda s, *a: False})()
            bar.wait()
            try: out[i] = ("ok", core.ingest({"checkpoint": self.cps[0][0], "signature": self.cps[0][1], "idempotency_key": "same"}))
            except Exception as exc: out[i] = ("err", exc)
        ts = [threading.Thread(target=worker, args=(i,)) for i in range(n)]; [t.start() for t in ts]; [t.join() for t in ts]
        self.assertEqual([o for o in out if o[0] == "err"], [])
        self.assertEqual(self.mk().count(), 1)
        self.assertEqual(len({r.anchor_id for _, (r, _c) in out}), 1)
        self.assertEqual(sum(c for _, (_r, c) in out), 1)

    def test_alembic_migration_matches_orm_after_upgrade(self):
        """Requires `alembic upgrade head` already applied to ANCHOR_TEST_DATABASE_URL."""
        from sqlalchemy import inspect
        insp = inspect(self.engine)
        self.assertIn("retained_checkpoints", insp.get_table_names())
        cols = {c["name"]: c["nullable"] for c in insp.get_columns("retained_checkpoints")}
        from anchor_service.models.db_models import RetainedCheckpoint as M
        self.assertEqual(set(cols), {c.name for c in M.__table__.columns})
        uq = {u["name"]: tuple(u["column_names"]) for u in insp.get_unique_constraints("retained_checkpoints")}
        self.assertEqual(uq["uq_chain_seq"], ("chain_id", "checkpoint_sequence"))
        self.assertEqual(uq["uq_idempotency_key"], ("idempotency_key",))

if __name__ == "__main__":
    unittest.main()
