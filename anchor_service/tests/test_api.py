"""HTTP-level tests. REQUIRE fastapi + httpx (pip install -r requirements.txt). Skipped
automatically when those are not installed. Uses the in-memory repository, so no PostgreSQL
is needed; PostgreSQL-specific behaviour is in test_api_postgres.py."""
import unittest
try:
    from fastapi.testclient import TestClient
    HAVE = True
except Exception:
    HAVE = False

from anchor_service.config import Settings
from anchor_service.services.core import InMemoryRepository
from anchor_service.tests.helpers import chain, new_keypair

TOKEN, TTOKEN = "unit-test-token", "unit-test-fault-token"


def make(test_mode=True):
    from anchor_service.main import create_app
    key, trusted = new_keypair()
    st = Settings("sqlite://", TOKEN, test_mode, TTOKEN, "", 8001, None, None)
    app = create_app(st, repo=InMemoryRepository(), trusted_keys=trusted)
    return TestClient(app), key


H = {"Authorization": f"Bearer {TOKEN}"}
def body(item, k): return {"checkpoint": item[0], "signature": item[1], "idempotency_key": k}


@unittest.skipUnless(HAVE, "fastapi/httpx not installed")
class ApiTests(unittest.TestCase):
    def setUp(self): self.c, self.key = make(); self.cps = chain(self.key, 3)

    def test_auth_required(self):
        self.assertEqual(self.c.post("/v1/checkpoints", json=body(self.cps[0], "a")).status_code, 401)
        self.assertEqual(self.c.post("/v1/checkpoints", json=body(self.cps[0], "a"),
                                     headers={"Authorization": "Bearer wrong"}).status_code, 401)
        self.assertEqual(self.c.get("/v1/chains/hms-main/checkpoints").status_code, 401)

    def test_post_get_list_and_status_codes(self):
        r = self.c.post("/v1/checkpoints", json=body(self.cps[0], "k1"), headers=H)
        self.assertEqual(r.status_code, 201); rec = r.json()["receipt"]
        again = self.c.post("/v1/checkpoints", json=body(self.cps[0], "k1"), headers=H)
        self.assertEqual((again.status_code, again.json()["receipt"]), (200, rec))
        self.assertEqual(self.c.get(f"/v1/checkpoints/{rec['anchor_id']}", headers=H).json()["checkpoint_digest"], rec["checkpoint_digest"])
        self.assertEqual(self.c.get("/v1/checkpoints/nope", headers=H).status_code, 404)
        self.assertEqual(self.c.post("/v1/checkpoints", json=body(self.cps[2], "k3"), headers=H).status_code, 409)  # gap
        bad = body(self.cps[1], "k2"); bad["signature"] = body(self.cps[2], "x")["signature"]
        self.assertEqual(self.c.post("/v1/checkpoints", json=bad, headers=H).status_code, 400)
        self.assertEqual(self.c.post("/v1/checkpoints", content=b"{oops", headers=H).status_code, 400)
        lst = self.c.get("/v1/chains/hms-main/checkpoints", headers=H).json()["checkpoints"]
        self.assertEqual([x["checkpoint_sequence"] for x in lst], [1])

    def test_ready_does_not_imply_retention(self):
        r = self.c.get("/health/ready"); self.assertEqual(r.status_code, 200)
        self.assertIn("does not imply", r.json()["note"])

    def test_fault_endpoint_hidden_when_not_test_mode(self):
        c, _ = make(test_mode=False)
        self.assertEqual(c.post("/v1/_test/fault", json={"mode": "down"}, headers={"X-Test-Token": TTOKEN}).status_code, 404)

    def test_fault_endpoint_needs_token_in_test_mode(self):
        self.assertEqual(self.c.post("/v1/_test/fault", json={"mode": "down"}).status_code, 401)
        self.assertEqual(self.c.post("/v1/_test/fault", json={"mode": "down"}, headers={"X-Test-Token": "bad"}).status_code, 401)
        self.assertEqual(self.c.post("/v1/_test/fault", json={"mode": "bogus"}, headers={"X-Test-Token": TTOKEN}).status_code, 400)

    def test_fault_endpoint_rejects_bad_values_with_400_not_500(self):
        FT = {"X-Test-Token": TTOKEN}
        for payload in ({"mode": "delay", "delay_seconds": "abc"}, {"mode": "delay", "delay_seconds": -1},
                        {"mode": "delay", "delay_seconds": 9999}, {"mode": 5}, [1, 2]):
            self.assertEqual(self.c.post("/v1/_test/fault", json=payload, headers=FT).status_code, 400, payload)
        self.assertEqual(self.c.post("/v1/_test/fault", content=b"{oops", headers=FT).status_code, 400)
        self.assertEqual(self.c.post("/v1/checkpoints", json=body(self.cps[0], "ok1"), headers=H).status_code, 201)  # still healthy

    def test_oversized_sequence_and_impossible_date_are_400(self):
        from anchor_service.tests.helpers import make_checkpoint, sign
        cp = make_checkpoint(1); cp["created_at"] = "2026-13-45T99:99:99.000000Z"; sg, _ = sign(cp, self.key)
        self.assertEqual(self.c.post("/v1/checkpoints", json={"checkpoint": cp, "signature": sg, "idempotency_key": "d"}, headers=H).status_code, 400)

    def test_down_mode_503_then_recover(self):
        FT = {"X-Test-Token": TTOKEN}
        self.c.post("/v1/_test/fault", json={"mode": "down"}, headers=FT)
        self.assertEqual(self.c.post("/v1/checkpoints", json=body(self.cps[0], "k"), headers=H).status_code, 503)
        self.c.post("/v1/_test/fault", json={"mode": "ok"}, headers=FT)
        self.assertEqual(self.c.post("/v1/checkpoints", json=body(self.cps[0], "k"), headers=H).status_code, 201)

    def test_drop_response_client_timeout_then_retry_returns_original(self):
        import httpx, time
        FT = {"X-Test-Token": TTOKEN}
        self.c.post("/v1/_test/fault", json={"mode": "drop_response", "delay_seconds": 1.0}, headers=FT)
        t = time.monotonic()
        r = self.c.post("/v1/checkpoints", json=body(self.cps[0], "lost"), headers=H)   # TestClient waits; server committed first
        self.assertGreaterEqual(time.monotonic() - t, 0.9)
        self.c.post("/v1/_test/fault", json={"mode": "ok"}, headers=FT)
        retry = self.c.post("/v1/checkpoints", json=body(self.cps[0], "lost"), headers=H)
        self.assertEqual(retry.json()["receipt"], r.json()["receipt"])
        self.assertEqual(len(self.c.get("/v1/chains/hms-main/checkpoints", headers=H).json()["checkpoints"]), 1)

if __name__ == "__main__":
    unittest.main()
