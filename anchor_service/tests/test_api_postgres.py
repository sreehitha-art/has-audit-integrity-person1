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
        mk = lambda aid, key: Record(aid, "hms-main", 1, 10, "a" * 64, None, dig, "{}", sig, "app-key-1",
                                     "2026-01-01T00:00:00.000000Z", "", key)
        repo.insert(mk("00000000-0000-0000-0000-000000000001", "x"))
        with self.assertRaises(Conflict): repo.insert(mk("00000000-0000-0000-0000-000000000002", "y"))  # chain/seq
        with self.assertRaises(Conflict): repo.insert(mk("00000000-0000-0000-0000-000000000003", "x"))  # idem key

if __name__ == "__main__":
    unittest.main()
