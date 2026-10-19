"""Hardening tests that run WITHOUT FastAPI/PostgreSQL: validation edge cases, race handling, concurrency
(threads), durability-before-receipt, restart persistence (separate process), commit-failure behaviour.
SQLite-backed tests use a stand-in DB (see sqlite_repository.py) - they do NOT prove PostgreSQL behaviour."""
import copy, os, subprocess, sys, tempfile, threading, unittest, json
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization
from anchor_service.services.core import (AnchorCore, BadRequest, Conflict, InMemoryRepository, InvalidSignature,
                                          Record, now_iso, Unavailable)
from anchor_service.services.checkpoint import CheckpointError, verify_signed_checkpoint, INT64_MAX
from anchor_service.services.sqlite_repository import SqliteRepository
from anchor_service.tests.helpers import chain, make_checkpoint, new_keypair, sign

REPO_ROOT = Path(__file__).resolve().parents[2]


def body(c, k): return {"checkpoint": c[0], "signature": c[1], "idempotency_key": k}


class NoLock:
    def __enter__(self): return self
    def __exit__(self, *a): return False


def unlocked_core(repo, trusted):
    """A core whose process-level ingest lock is disabled: models several worker processes whose only
    shared arbiter is the database's unique constraints."""
    c = AnchorCore(repo, trusted); c._ingest_lock = NoLock(); return c


class StaleReadRepo(InMemoryRepository):
    """Models the check-then-insert race: the first ``stale_budget`` reads (the three pre-insert lookups in
    AnchorCore._process) miss, as if a concurrent worker had not yet committed when we looked; the
    'database' constraint check on insert always sees the truth, and reads after the conflict are fresh."""
    stale_budget = 0
    def _stale(s):
        if s.stale_budget > 0: s.stale_budget -= 1; return True
        return False
    def get_by_idempotency_key(s, k): return None if s._stale() else super().get_by_idempotency_key(k)
    def get_by_chain_seq(s, c, q): return None if s._stale() else super().get_by_chain_seq(c, q)
    def last_in_chain(s, c): return None if s._stale() else super().last_in_chain(c)
    def insert(s, rec):
        for r in list(s.by_id.values()):
            if r.idempotency_key == rec.idempotency_key: raise Conflict("idempotency_key already used")
            if (r.chain_id, r.checkpoint_sequence) == (rec.chain_id, rec.checkpoint_sequence):
                raise Conflict("(chain_id, checkpoint_sequence) already retained")
        rec.durably_retained_at = now_iso(); s.by_id[rec.anchor_id] = rec; return rec


class ValidationHardening(unittest.TestCase):
    def setUp(self): self.key, self.trusted = new_keypair()

    def _check(self, cp):
        sig, _ = sign(cp, self.key); return verify_signed_checkpoint(cp, sig, self.trusted)

    def test_impossible_calendar_values_rejected(self):
        for ts in ["2026-13-45T99:99:99.000000Z", "2026-02-30T00:00:00Z", "2026-01-01T24:00:00Z", "2026-01-01T00:60:00Z"]:
            cp = make_checkpoint(1); cp["created_at"] = ts
            with self.assertRaises(CheckpointError, msg=ts): self._check(cp)
        for ts in ["2026-01-01T00:00:00Z", "2026-02-28T23:59:59.999999Z", "2024-02-29T00:00:00Z"]:   # valid incl. leap day
            cp = make_checkpoint(1); cp["created_at"] = ts; self._check(cp)

    def test_int64_bounds(self):
        self._check(make_checkpoint(INT64_MAX, "a" * 64, terminal_seq=INT64_MAX))
        for kw in (dict(seq=INT64_MAX + 1), dict(seq=2 ** 70)):
            with self.assertRaises(CheckpointError): self._check(make_checkpoint(kw["seq"], "a" * 64, terminal_seq=5))
        with self.assertRaises(CheckpointError): self._check(make_checkpoint(2, "a" * 64, terminal_seq=INT64_MAX + 1))

    def test_fault_inputs_validated(self):
        core = AnchorCore(InMemoryRepository(), self.trusted)
        for bad in [float("nan"), float("inf"), -1, 601, "abc", None, True, [1]]:
            with self.assertRaises(BadRequest, msg=repr(bad)): core.set_fault("delay", bad)
        for bad in [None, 5, "", "DOWN", ["down"]]:
            with self.assertRaises(BadRequest, msg=repr(bad)): core.set_fault(bad, 0)
        self.assertEqual(core.set_fault("delay", 2), {"mode": "delay", "delay_seconds": 2.0})
        self.assertEqual(core.fault.mode, "delay")

    def test_failed_validation_leaves_fault_state_unchanged(self):
        core = AnchorCore(InMemoryRepository(), self.trusted); core.set_fault("down")
        with self.assertRaises(BadRequest): core.set_fault("delay", float("nan"))
        self.assertEqual(core.fault.mode, "down")


class RaceResolution(unittest.TestCase):
    def setUp(self):
        self.key, self.trusted = new_keypair(); self.cps = chain(self.key, 3)

    def test_race_loser_same_key_same_content_gets_original_receipt(self):
        repo = StaleReadRepo(); core = AnchorCore(repo, self.trusted)
        first, _ = core.ingest(body(self.cps[0], "k")); repo.stale_budget = 3
        again, created = core.ingest(body(self.cps[0], "k"))
        self.assertFalse(created); self.assertEqual(again.receipt(), first.receipt()); self.assertEqual(repo.count(), 1)

    def test_race_loser_different_key_same_content_gets_original_receipt(self):
        repo = StaleReadRepo(); core = AnchorCore(repo, self.trusted)
        first, _ = core.ingest(body(self.cps[0], "k1")); repo.stale_budget = 3
        again, created = core.ingest(body(self.cps[0], "k2"))
        self.assertFalse(created); self.assertEqual(again.anchor_id, first.anchor_id)

    def test_race_loser_same_key_different_content_is_409(self):
        repo = StaleReadRepo(); core = AnchorCore(repo, self.trusted)
        core.ingest(body(self.cps[0], "k")); repo.stale_budget = 3
        other = make_checkpoint(1, None, terminal_seq=777); sg, _ = sign(other, self.key)
        with self.assertRaises(Conflict) as cm: core.ingest({"checkpoint": other, "signature": sg, "idempotency_key": "k"})
        self.assertIn("different checkpoint content", cm.exception.message)       # came from the re-resolve branch
        self.assertEqual(repo.count(), 1)

    def test_race_loser_same_sequence_different_content_is_409(self):
        repo = StaleReadRepo(); core = AnchorCore(repo, self.trusted)
        core.ingest(body(self.cps[0], "k1")); repo.stale_budget = 3
        other = make_checkpoint(1, None, terminal_seq=777); sg, _ = sign(other, self.key)
        with self.assertRaises(Conflict): core.ingest({"checkpoint": other, "signature": sg, "idempotency_key": "k2"})


class ConcurrencyInMemory(unittest.TestCase):
    def setUp(self):
        self.key, self.trusted = new_keypair(); self.cps = chain(self.key, 6)

    def _hammer(self, fn, n):
        out, bar = [None] * n, threading.Barrier(n)
        def w(i):
            bar.wait()
            try: out[i] = ("ok", fn(i))
            except Exception as exc: out[i] = ("err", exc)
        ts = [threading.Thread(target=w, args=(i,)) for i in range(n)]
        [t.start() for t in ts]; [t.join() for t in ts]; return out

    def test_same_request_concurrently_yields_one_record_one_receipt(self):
        repo = InMemoryRepository(); core = AnchorCore(repo, self.trusted)
        out = self._hammer(lambda i: core.ingest(body(self.cps[0], "same")), 48)
        self.assertTrue(all(k == "ok" for k, _ in out), [o for o in out if o[0] == "err"][:1])
        self.assertEqual(repo.count(), 1)
        self.assertEqual({r.anchor_id for _, (r, _c) in out}, {repo.list_chain("hms-main")[0].anchor_id})
        self.assertEqual(sum(c for _, (_r, c) in out), 1)                       # exactly one 'created'

    def test_conflicting_contents_same_sequence_exactly_one_wins(self):
        repo = InMemoryRepository(); core = AnchorCore(repo, self.trusted)
        variants = []
        for i in range(24):
            cp = make_checkpoint(1, None, terminal_seq=100 + i); sg, _ = sign(cp, self.key); variants.append((cp, sg))
        out = self._hammer(lambda i: core.ingest(body(variants[i], f"k{i}")), 24)
        self.assertEqual(sum(k == "ok" for k, _ in out), 1)
        self.assertTrue(all(isinstance(e, Conflict) for k, e in out if k == "err"))
        self.assertEqual(repo.count(), 1)

    def test_out_of_order_concurrent_submission_never_creates_gaps(self):
        repo = InMemoryRepository(); core = AnchorCore(repo, self.trusted)
        self._hammer(lambda i: core.ingest(body(self.cps[5 - i], f"k{i}")), 6)    # submitted reverse order
        seqs = [r.checkpoint_sequence for r in repo.list_chain("hms-main")]
        self.assertEqual(seqs, list(range(1, len(seqs) + 1)))                    # contiguous prefix, whatever won
        prev = None
        for r in repo.list_chain("hms-main"):
            self.assertEqual(r.previous_checkpoint_digest, prev); prev = r.checkpoint_digest


class SqliteDurability(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.db = os.path.join(self.tmp.name, "anchor.sqlite3")
        self.key, self.trusted = new_keypair(); self.cps = chain(self.key, 4)
    def tearDown(self): self.tmp.cleanup()

    def test_receipt_returned_only_after_commit_visible_to_independent_connection(self):
        repo = SqliteRepository(self.db); core = AnchorCore(repo, self.trusted)
        rec, _ = core.ingest(body(self.cps[0], "k"))
        other = SqliteRepository(self.db)                               # brand-new connection/repo
        got = other.get_by_id(rec.anchor_id)
        self.assertIsNotNone(got); self.assertEqual(got.receipt(), rec.receipt())
        self.assertTrue(rec.received_at <= rec.durably_retained_at)

    def test_commit_failure_means_no_receipt_and_no_row(self):
        def boom(): raise RuntimeError("disk full (injected)")
        repo = SqliteRepository(self.db, commit_hook=boom); core = AnchorCore(repo, self.trusted)
        with self.assertRaises(RuntimeError): core.ingest(body(self.cps[0], "k"))
        self.assertEqual(SqliteRepository(self.db).count(), 0)           # rolled back
        repo.commit_hook = None                                          # recovery: same request now succeeds
        rec, created = core.ingest(body(self.cps[0], "k")); self.assertTrue(created)
        self.assertEqual(SqliteRepository(self.db).count(), 1)

    def test_db_constraints_enforced_below_the_application(self):
        repo = SqliteRepository(self.db); cp, sg, dg = self.cps[0]
        mk = lambda aid, key, seq: Record(aid, "hms-main", seq, 10, "a" * 64, None, dg, "{}", sg, "app-key-1",
                                          "2026-01-01T00:00:00.000000Z", "", key)
        repo.insert(mk("id1", "x", 1))
        with self.assertRaises(Conflict): repo.insert(mk("id2", "y", 1))     # (chain, seq) duplicate only
        with self.assertRaises(Conflict): repo.insert(mk("id3", "x", 2))     # idempotency-key duplicate only
        self.assertEqual(repo.count(), 1)

    def test_restart_persistence_in_separate_process_and_retry_returns_original(self):
        cp, sg, dg = self.cps[0]
        script = (
            "import sys, json\n"
            "from anchor_service.services.core import AnchorCore\n"
            "from anchor_service.services.sqlite_repository import SqliteRepository\n"
            "from cryptography.hazmat.primitives.serialization import load_pem_public_key\n"
            "d = json.load(sys.stdin)\n"
            "pub = load_pem_public_key(d['pub'].encode())\n"
            "core = AnchorCore(SqliteRepository(d['db']), {'app-key-1': pub})\n"
            "rec, created = core.ingest({'checkpoint': d['cp'], 'signature': d['sig'], 'idempotency_key': 'persist'})\n"
            "print(json.dumps({'created': created, 'receipt': rec.receipt()}))\n")
        pub = self.key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
        payload = json.dumps({"db": self.db, "cp": cp, "sig": sg, "pub": pub})
        run = lambda: json.loads(subprocess.run([sys.executable, "-c", script], input=payload, capture_output=True, text=True,
                                                cwd=REPO_ROOT, check=True).stdout)
        first = run()                                                    # process 1 writes then EXITS
        self.assertTrue(first["created"])
        second = run()                                                   # process 2 = restart + client retry
        self.assertFalse(second["created"]); self.assertEqual(second["receipt"], first["receipt"])
        self.assertEqual(SqliteRepository(self.db).count(), 1)

    def test_multi_worker_race_same_request_one_row_one_receipt(self):
        """Several 'workers' (separate AnchorCore + no shared lock) racing on the same request: the DB
        constraint arbitrates; losers must receive the original receipt, never a 409/500."""
        n = 12; bar = threading.Barrier(n); out = [None] * n
        def worker(i):
            core = unlocked_core(SqliteRepository(self.db), self.trusted); bar.wait()
            try: out[i] = ("ok", core.ingest(body(self.cps[0], "same")))
            except Exception as exc: out[i] = ("err", exc)
        ts = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
        [t.start() for t in ts]; [t.join() for t in ts]
        errs = [o for o in out if o[0] == "err"]
        self.assertEqual(errs, [], errs[:1])
        self.assertEqual(SqliteRepository(self.db).count(), 1)
        self.assertEqual(len({r.anchor_id for _, (r, _c) in out}), 1)
        self.assertEqual(len({json.dumps(r.receipt(), sort_keys=True) for _, (r, _c) in out}), 1)   # identical receipts
        self.assertEqual(sum(c for _, (_r, c) in out), 1)

    def test_multi_worker_race_conflicting_content_exactly_one_wins(self):
        n = 10; bar = threading.Barrier(n); out = [None] * n
        variants = []
        for i in range(n):
            cp = make_checkpoint(1, None, terminal_seq=200 + i); sg, _ = sign(cp, self.key); variants.append((cp, sg))
        def worker(i):
            core = unlocked_core(SqliteRepository(self.db), self.trusted); bar.wait()
            try: out[i] = ("ok", core.ingest(body(variants[i], f"k{i}")))
            except Exception as exc: out[i] = ("err", exc)
        ts = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
        [t.start() for t in ts]; [t.join() for t in ts]
        self.assertEqual(sum(k == "ok" for k, _ in out), 1)
        self.assertTrue(all(isinstance(e, Conflict) for k, e in out if k == "err"), [e for k, e in out if k == "err"][:1])
        self.assertEqual(SqliteRepository(self.db).count(), 1)

    def test_full_chain_over_sqlite_then_signature_tampering_rejected(self):
        core = AnchorCore(SqliteRepository(self.db), self.trusted)
        for i, c in enumerate(self.cps): core.ingest(body(c, f"k{i}"))
        self.assertEqual([r.checkpoint_sequence for r in core.repo.list_chain("hms-main")], [1, 2, 3, 4])
        nxt = make_checkpoint(5, self.cps[3][2]); sg, _ = sign(nxt, self.key)
        bad = copy.deepcopy(nxt); bad["terminal_event_sequence"] += 1
        with self.assertRaises(InvalidSignature): core.ingest({"checkpoint": bad, "signature": sg, "idempotency_key": "t"})
        forged, _ = sign(nxt, Ed25519PrivateKey.generate())
        with self.assertRaises(InvalidSignature): core.ingest({"checkpoint": nxt, "signature": forged, "idempotency_key": "t2"})
        self.assertEqual(core.repo.count(), 4)

if __name__ == "__main__":
    unittest.main()
