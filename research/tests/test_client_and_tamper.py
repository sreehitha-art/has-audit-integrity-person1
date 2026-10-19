import asyncio, json, tempfile, unittest
from pathlib import Path
from research.experiment_config import ExperimentConfig
from research.telemetry import Telemetry
from research.workload_client import MockHmsClient, run_workload
from research.tampering import (assert_safe_target, UnsafeTargetError, run_scenario, MockVerifier,
                                SCENARIOS, HmsVerifierAdapter)


def cfg(**kw):
    base = dict(run_id="rc", seed=3, duration_s=20, warmup_s=2, policy_name="A", policy_params={"T": 5},
                workload_type="poisson", target_rate=10, n_clients=4,
                risk_mixture={"low": 0.6, "medium": 0.3, "high": 0.1})
    base.update(kw); return ExperimentConfig(**base)


class ClientTests(unittest.TestCase):
    def run_it(self, client, **kw):
        with tempfile.TemporaryDirectory() as d:
            tel = Telemetry("rc", d)
            summary = asyncio.run(run_workload(cfg(**kw), client, tel, time_scale=0.01))
            tel.close()
            lines = [json.loads(l) for l in (Path(d) / "rc" / "events.jsonl").read_text().splitlines()]
        return summary, lines

    def test_all_requests_recorded_and_labelled_mock(self):
        s, lines = self.run_it(MockHmsClient())
        self.assertEqual(len(lines), s["n_sent"]); self.assertEqual(s["n_sent"], s["n_planned"])
        self.assertTrue(s["mock_client"]); self.assertTrue(all(l["mock"] for l in lines))
        self.assertTrue(all(l["event_type"] == "request_done" and l["seed"] == 3 and l["run_id"] == "rc"
                            for l in lines))
        self.assertEqual(s["success_rate"], 1.0)

    def test_failures_and_timeouts_are_kept(self):
        s, lines = self.run_it(MockHmsClient(fail_every=5, timeout_every=7))
        self.assertGreater(s["n_failed"], 0); self.assertGreater(s["n_timeouts"], 0)
        self.assertLess(s["success_rate"], 1.0)
        self.assertEqual(len(lines), s["n_planned"])          # nothing discarded
        self.assertTrue(any(l["error"] == "timeout" and l["status"] is None for l in lines))
        self.assertIn("500", s["status_counts"])

    def test_latency_fields_and_warmup_excluded(self):
        s, lines = self.run_it(MockHmsClient())
        self.assertGreater(s["latency_ms_p50"], 0); self.assertGreaterEqual(s["latency_ms_p99"], s["latency_ms_p50"])
        self.assertLess(s["n_measured"], s["n_sent"])

    def test_crashing_client_counted_as_failure(self):
        class Boom(MockHmsClient):
            async def send(self, *a): raise RuntimeError("x")
        s, _ = self.run_it(Boom())
        self.assertEqual(s["n_ok"], 0); self.assertEqual(s["n_failed"], s["n_measured"])


class TamperSafetyTests(unittest.TestCase):
    ENV = {"RESEARCH_MODE": "1"}

    def test_refuses_without_research_mode(self):
        with self.assertRaises(UnsafeTargetError): assert_safe_target("hms_research", {})
        with self.assertRaises(UnsafeTargetError): assert_safe_target("hms_research", {"RESEARCH_MODE": "0"})

    def test_refuses_every_other_database(self):
        for db in ["hms_dev", "anchor_dev", "hms_template", "postgres", "HMS_RESEARCH", "hms_research ",
                   "hms_research2", "x_hms_research", "", "hms_research;drop"]:
            with self.assertRaises(UnsafeTargetError, msg=db): assert_safe_target(db, self.ENV)
        assert_safe_target("hms_research", self.ENV)

    def test_mutator_never_called_when_unsafe(self):
        class M:
            called = False
            def apply(self, s, db): M.called = True; return {}
        with self.assertRaises(UnsafeTargetError):
            run_scenario("delete_event", "hms_dev", M(), MockVerifier(), env=self.ENV)
        self.assertFalse(M.called)

    def test_expectations_and_mock_labelling(self):
        class M:
            def apply(self, s, db): return {"scenario": s}
        v = MockVerifier({"modify_payload": "mismatch", "rewrite_unanchored_suffix": "verified",
                          "anchor_unavailable": "inconclusive", "delete_event": "verified"})
        with tempfile.TemporaryDirectory() as d:
            tel = Telemetry("t", d)
            r1 = run_scenario("modify_payload", "hms_research", M(), v, tel, env=self.ENV)
            r2 = run_scenario("rewrite_unanchored_suffix", "hms_research", M(), v, tel, env=self.ENV)
            r3 = run_scenario("anchor_unavailable", "hms_research", M(), v, tel, env=self.ENV)
            r4 = run_scenario("delete_event", "hms_research", M(), v, tel, env=self.ENV)  # false 'verified'
            r5 = run_scenario("edit_business_row", "hms_research", M(), v, tel, env=self.ENV)
            tel.close()
            evs = [json.loads(l)["event_type"] for l in (Path(d) / "t" / "events.jsonl").read_text().splitlines()]
        self.assertTrue(r1.matches_expectation); self.assertTrue(r2.matches_expectation)
        self.assertTrue(r3.matches_expectation)
        self.assertFalse(r4.matches_expectation)       # undetected anchored tamper = failure
        self.assertIsNone(r5.matches_expectation)      # conditional on verifier scope
        self.assertTrue(all(r.verifier_source == "mock" for r in (r1, r2, r3, r4, r5)))
        self.assertEqual(evs.count("tamper_applied"), 5)

    def test_all_seven_plus_one_scenarios_defined(self):
        self.assertEqual(len(SCENARIOS), 8)

    def test_hms_adapter_is_unimplemented_placeholder(self):
        with self.assertRaises(NotImplementedError): HmsVerifierAdapter().verify()

if __name__ == "__main__":
    unittest.main()
