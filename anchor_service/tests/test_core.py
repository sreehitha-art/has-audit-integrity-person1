import time, unittest
from anchor_service.services.core import (AnchorCore, InMemoryRepository, BadRequest,
    InvalidSignature, Conflict, Unavailable, Record)
from anchor_service.tests.helpers import chain, make_checkpoint, sign, new_keypair
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


class RepoStamp(InMemoryRepository):
    """Sets durably_retained_at at 'commit' like the SQL repository does."""
    def insert(self, rec):
        from anchor_service.services.core import now_iso
        rec.durably_retained_at = now_iso()
        return super().insert(rec)


def body(cp, sig, key="k1"):
    return {"checkpoint": cp, "signature": sig, "idempotency_key": key}


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.key, self.trusted = new_keypair()
        self.repo = RepoStamp()
        self.events = []
        self.core = AnchorCore(self.repo, self.trusted,
                               on_fault_event=lambda t, **k: self.events.append((t, k)))
        self.cps = chain(self.key, 4)

    def post(self, i, key=None):
        cp, sig, _ = self.cps[i]
        return self.core.ingest(body(cp, sig, key or f"idem-{i+1}"))

    def test_valid_accepted_with_receipt(self):
        rec, created = self.post(0)
        self.assertTrue(created)
        r = rec.receipt()
        self.assertEqual(r["checkpoint_digest"], self.cps[0][2])
        self.assertTrue(r["durably_retained_at"])

    def test_invalid_signature_rejected_and_not_stored(self):
        cp, _, _ = self.cps[0]
        _, bad = sign(cp, Ed25519PrivateKey.generate())[0], None
        badsig = sign(cp, Ed25519PrivateKey.generate())[0]
        with self.assertRaises(InvalidSignature):
            self.core.ingest(body(cp, badsig))
        self.assertEqual(self.repo.count(), 0)

    def test_malformed_and_missing_idempotency_key(self):
        cp, sig, _ = self.cps[0]
        with self.assertRaises(BadRequest): self.core.ingest({"checkpoint": cp, "signature": sig})
        with self.assertRaises(BadRequest): self.core.ingest("nope")
        bad = dict(cp); del bad["chain_id"]
        with self.assertRaises(BadRequest): self.core.ingest(body(bad, sig))

    def test_idempotent_retry_returns_original_single_record(self):
        r1, c1 = self.post(0, "same")
        r2, c2 = self.post(0, "same")
        self.assertTrue(c1); self.assertFalse(c2)
        self.assertEqual(r1.receipt(), r2.receipt())
        self.assertEqual(self.repo.count(), 1)

    def test_same_key_different_content_conflict(self):
        self.post(0, "same")
        cp, sig, _ = self.cps[1]
        with self.assertRaises(Conflict): self.core.ingest(body(cp, sig, "same"))
        self.assertEqual(self.repo.count(), 1)

    def test_same_sequence_conflicting_content_rejected(self):
        self.post(0)
        other = make_checkpoint(1, None, terminal_seq=999)
        sig, _ = sign(other, self.key)
        with self.assertRaises(Conflict): self.core.ingest(body(other, sig, "different-key"))
        self.assertEqual(self.repo.count(), 1)

    def test_sequence_gap_rejected(self):
        self.post(0)
        with self.assertRaises(Conflict) as cm: self.post(2)
        self.assertIn("gap", cm.exception.message)

    def test_first_checkpoint_must_be_sequence_1(self):
        with self.assertRaises(Conflict): self.post(1)

    def test_wrong_previous_digest_rejected(self):
        self.post(0)
        cp = make_checkpoint(2, "f" * 64); sig, _ = sign(cp, self.key)
        with self.assertRaises(Conflict): self.core.ingest(body(cp, sig, "x"))

    def test_chains_are_independent(self):
        other = chain(self.key, 1, chain_id="other-chain")
        self.post(0)
        rec, created = self.core.ingest(body(other[0][0], other[0][1], "o1"))
        self.assertTrue(created)
        self.assertEqual(len(self.repo.list_chain("other-chain")), 1)

    def test_full_chain_in_order(self):
        for i in range(4): self.post(i)
        seqs = [r.checkpoint_sequence for r in self.repo.list_chain("hms-main")]
        self.assertEqual(seqs, [1, 2, 3, 4])

    def test_down_mode_returns_503_and_stores_nothing(self):
        self.core.set_fault("down")
        with self.assertRaises(Unavailable): self.post(0)
        self.assertEqual(self.repo.count(), 0)
        self.assertEqual(self.events[0][0], "anchor_fault_start")

    def test_delay_mode_delays_processing(self):
        self.core.set_fault("delay", 0.2)
        t = time.monotonic(); self.post(0)
        self.assertGreaterEqual(time.monotonic() - t, 0.19)

    def test_drop_response_commits_then_retry_returns_original(self):
        self.core.set_fault("drop_response", 5.0)
        rec, created, post_delay = self.core.ingest_with_fault(
            body(self.cps[0][0], self.cps[0][1], "lost"))
        self.assertTrue(created); self.assertEqual(post_delay, 5.0)   # stored, response would be held
        self.assertEqual(self.repo.count(), 1)                        # durable before response
        self.core.set_fault("ok")                                      # recovery
        rec2, created2 = self.core.ingest(body(self.cps[0][0], self.cps[0][1], "lost"))
        self.assertFalse(created2)
        self.assertEqual(rec.receipt(), rec2.receipt())
        self.assertEqual(self.repo.count(), 1)
        self.assertEqual([e[0] for e in self.events],
                         ["anchor_fault_start", "anchor_fault_end"])

    def test_invalid_fault_params(self):
        with self.assertRaises(BadRequest): self.core.set_fault("explode")
        with self.assertRaises(BadRequest): self.core.set_fault("delay", -1)

if __name__ == "__main__":
    unittest.main()
