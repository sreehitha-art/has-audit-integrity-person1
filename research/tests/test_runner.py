import json, tempfile, unittest
from pathlib import Path
from research.experiment_config import ExperimentConfig, OutageWindow
from research.scripts.run_experiment import run
from research.outage import run_outage_schedule
from research.telemetry import Telemetry


def cfg(**kw):
    base = dict(run_id="rx", seed=1, duration_s=10, warmup_s=1, policy_name="B", policy_params={"N": 5},
                workload_type="poisson", target_rate=10, n_clients=2,
                risk_mixture={"low": 1.0, "medium": 0.0, "high": 0.0},
                outages=(OutageWindow(2, 4, "down", label="o1"),))
    base.update(kw); return ExperimentConfig(**base)


class RunnerTests(unittest.TestCase):
    def test_mock_run_produces_manifest_raw_logs_and_is_labelled_mock(self):
        with tempfile.TemporaryDirectory() as d:
            m = run(cfg(), "mock", Path(d), time_scale=0.02)
            man = json.loads((Path(d) / "rx" / "manifest.json").read_text())
            ev = [json.loads(l) for l in (Path(d) / "rx" / "events.jsonl").read_text().splitlines()]
            self.assertTrue((Path(d) / "rx" / "config.json").exists())
        self.assertEqual(man["status"], "mock_completed")          # never plain "completed"
        self.assertEqual(man["config_sha256"], cfg().digest()); self.assertEqual(man["seed"], 1)
        types = [e["event_type"] for e in ev]
        self.assertEqual(types[0], "experiment_start"); self.assertEqual(types[-1], "experiment_end")
        self.assertIn("outage_start", types); self.assertIn("outage_end", types)
        self.assertTrue(all(e["mock"] for e in ev if e["event_type"] == "request_done"))

    def test_real_mode_is_incomplete_not_success(self):
        import os; os.environ["RESEARCH_MODE"] = "1"
        try:
            with tempfile.TemporaryDirectory() as d:
                m = run(cfg(), "real", Path(d))
                self.assertEqual(m["status"], "incomplete")
                self.assertTrue((Path(d) / "rx" / "events.jsonl").exists())   # raw log kept
        finally: del os.environ["RESEARCH_MODE"]

    def test_real_mode_refused_without_research_mode(self):
        import os; os.environ.pop("RESEARCH_MODE", None)
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(run(cfg(), "real", Path(d))["status"], "failed")

    def test_failed_run_preserves_raw_log_and_marks_failed(self):
        import research.scripts.run_experiment as r
        orig = r.plan_schedule
        r.plan_schedule = lambda c: (_ for _ in ()).throw(RuntimeError("boom"))
        try:
            with tempfile.TemporaryDirectory() as d:
                m = run(cfg(), "mock", Path(d))
                self.assertEqual(m["status"], "failed")
                lines = (Path(d) / "rx" / "events.jsonl").read_text().splitlines()
                self.assertEqual(json.loads(lines[-1])["status"], "failed")
        finally: r.plan_schedule = orig

    def test_outage_planned_vs_observed_logged_separately(self):
        calls = []; t = [0.0]
        with tempfile.TemporaryDirectory() as d:
            tel = Telemetry("o", d)
            obs = run_outage_schedule((OutageWindow(1, 3, "down", label="a"),),
                                      lambda m, s: calls.append(m), tel,
                                      sleep=lambda s: t.__setitem__(0, t[0] + s), clock=lambda: t[0])
            tel.close()
            ev = [json.loads(l) for l in (Path(d) / "o" / "events.jsonl").read_text().splitlines()]
        self.assertEqual(calls, ["down", "ok"])
        s, e = ev
        self.assertEqual((s["planned_start_s"], s["planned_end_s"]), (1, 3))
        self.assertIn("observed_start_utc", s); self.assertIn("observed_end_utc", e)
        self.assertIn("observed_duration_s", e)

if __name__ == "__main__":
    unittest.main()
