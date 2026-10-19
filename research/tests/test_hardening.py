import json, math, os, subprocess, sys, tempfile, unittest
from pathlib import Path
import numpy as np
from research import telemetry
from research.telemetry import Telemetry
from research.experiment_config import ExperimentConfig, ConfigError, BurstSettings, OutageWindow
from research.workload import plan_schedule, schedule_digest
from research.outage import run_outage_schedule
from research.manifest import collect_environment, package_versions
from research.scripts.run_experiment import run
from research.analysis import metrics as m

REPO = Path(__file__).resolve().parents[2]


def cfg(**kw):
    base = dict(run_id="h1", seed=42, duration_s=120, warmup_s=2, policy_name="B", policy_params={"N": 10},
                workload_type="poisson", target_rate=10, risk_mixture={"low": 0.6, "medium": 0.3, "high": 0.1})
    base.update(kw); return ExperimentConfig(**base)


class TelemetryHardening(unittest.TestCase):
    def test_reserved_fields_cannot_be_overridden_and_never_raise(self):
        with tempfile.TemporaryDirectory() as d:
            t = Telemetry("r", d)
            rec = t.emit("x", ts_utc="FORGED", mono_ns=0, run_id="other", event_type="spoof", keep=1)
            t.close()
            self.assertNotEqual(rec["ts_utc"], "FORGED"); self.assertGreater(rec["mono_ns"], 0)
            self.assertEqual((rec["run_id"], rec["event_type"]), ("r", "x"))
            self.assertEqual(rec["field_ts_utc"], "FORGED"); self.assertEqual(rec["keep"], 1)
            self.assertEqual(rec["reserved_field_conflicts"], ["event_type", "mono_ns", "run_id", "ts_utc"])
            self.assertEqual(json.loads((Path(d) / "r" / "events.jsonl").read_text())["run_id"], "r")

    def test_module_level_emit_same_protection(self):
        with tempfile.TemporaryDirectory() as d:
            telemetry.start_run("m", d)
            try: rec = telemetry.emit("x", event_type="spoof")
            finally: telemetry.end_run()
            self.assertEqual(rec["event_type"], "x")


class ConfigHardening(unittest.TestCase):
    def test_bad_types_and_nonfinite_become_config_errors(self):
        bad = [dict(target_rate=float("nan")), dict(target_rate="10"), dict(duration_s=float("inf")), dict(seed=1.5),
               dict(seed=True), dict(n_clients="2"), dict(run_id=7), dict(policy_params=[1]),
               dict(policy_params={"x": float("nan")}), dict(risk_mixture={"low": "1.0"}),
               dict(risk_mixture={"low": float("nan"), "medium": 0.5, "high": 0.5}),
               dict(workload_type="bursty", burst=BurstSettings(float("inf"), 1, 1, 1)),
               dict(outages=(OutageWindow(float("nan"), 5),)), dict(rate_tolerance=None)]
        for kw in bad:
            with self.assertRaises(ConfigError, msg=str(kw)): cfg(**kw)

    def test_valid_still_valid(self):
        self.assertEqual(len(cfg().digest()), 64)


class WorkloadReproducibility(unittest.TestCase):
    SNIPPET = ("import sys; from research.experiment_config import ExperimentConfig;"
               "from research.workload import plan_schedule, schedule_digest;"
               "c=ExperimentConfig.from_dict(__import__('json').loads(sys.argv[1]));"
               "print(schedule_digest(plan_schedule(c)))")

    def _digest_in_subprocess(self, c, hashseed):
        env = {**os.environ, "PYTHONHASHSEED": str(hashseed), "PYTHONPATH": str(REPO)}
        return subprocess.run([sys.executable, "-c", self.SNIPPET, json.dumps(c.to_dict())], capture_output=True,
                              text=True, check=True, env=env, cwd=REPO).stdout.strip()

    def test_identical_across_processes_and_hash_seeds(self):
        for wt, extra in [("steady", {}), ("poisson", {}), ("bursty", dict(burst=BurstSettings(50, 5, 5, 15)))]:
            c = cfg(workload_type=wt, **extra); here = schedule_digest(plan_schedule(c))
            self.assertEqual({self._digest_in_subprocess(c, hs) for hs in (0, 1, 12345)}, {here}, wt)

    def test_policy_independence_same_workload_for_every_policy(self):
        digs = {schedule_digest(plan_schedule(cfg(policy_name=p, policy_params={}))) for p in ("A", "B", "C-risk", "C-unit")}
        self.assertEqual(len(digs), 1)       # policy must never change the planned load (paired comparisons)

    def test_golden_digest_pinned_to_numpy_version(self):
        ver = np.__version__
        d = schedule_digest(plan_schedule(cfg()))
        golden_file = REPO / "research" / "tests" / "golden_schedule_digests.json"
        golden = json.loads(golden_file.read_text()) if golden_file.exists() else {}
        if ver not in golden:
            self.skipTest(f"no golden digest recorded for numpy {ver}; recorded value would be {d}")
        self.assertEqual(golden[ver], d, "RNG stream changed for this numpy version - schedules are no longer reproducible")

    def test_manifest_records_package_versions(self):
        env = collect_environment(REPO)
        self.assertEqual(env["packages"]["numpy"], np.__version__)
        self.assertIn("fastapi", package_versions())


class OutageHardening(unittest.TestCase):
    def _run(self, set_fault, should_stop=lambda: False, outages=(OutageWindow(1, 3, "down", label="a"),)):
        t = [0.0]
        with tempfile.TemporaryDirectory() as d:
            tel = Telemetry("o", d)
            try:
                run_outage_schedule(outages, set_fault, tel, sleep=lambda s: t.__setitem__(0, t[0] + s),
                                    clock=lambda: t[0], should_stop=should_stop)
                err = None
            except Exception as exc: err = exc
            tel.close()
            ev = [json.loads(l) for l in (Path(d) / "o" / "events.jsonl").read_text().splitlines()]
        return err, ev

    def test_failure_applying_fault_is_raised_and_logged(self):
        def sf(mode, d): raise ConnectionError("anchor unreachable")
        err, ev = self._run(sf)
        self.assertIsInstance(err, ConnectionError)
        self.assertEqual([e["event_type"] for e in ev], ["outage_error"])

    def test_failure_clearing_fault_still_attempts_restore_and_logs_abort(self):
        calls = []
        def sf(mode, d):
            calls.append(mode)
            if mode == "ok" and calls.count("ok") == 1: raise ConnectionError("flaky")
        err, ev = self._run(sf)
        self.assertIsInstance(err, ConnectionError)
        self.assertEqual(calls, ["down", "ok", "ok"])                       # second 'ok' = best-effort restore
        types = [e["event_type"] for e in ev]
        self.assertIn("outage_error", types); self.assertTrue([e for e in ev if e["event_type"] == "outage_end"][-1]["aborted"])

    def test_abort_between_windows_does_not_start_next_fault(self):
        calls = []; flag = {"stop": False}
        def sf(mode, d):
            calls.append(mode)
            if mode == "ok": flag["stop"] = True
        err, ev = self._run(sf, should_stop=lambda: flag["stop"],
                            outages=(OutageWindow(1, 2, "down", label="a"), OutageWindow(3, 4, "down", label="b")))
        self.assertIsNone(err); self.assertEqual(calls, ["down", "ok"])

    def test_runner_marks_run_failed_when_outage_cannot_be_applied(self):
        def sf(mode, d): raise ConnectionError("anchor unreachable")
        with tempfile.TemporaryDirectory() as d:
            man = run(cfg(duration_s=10, warmup_s=1, target_rate=5, n_clients=1,
                          outages=(OutageWindow(2, 4, "down", label="o"),),
                          risk_mixture={"low": 1.0, "medium": 0.0, "high": 0.0}), "mock", Path(d), time_scale=0.02, set_fault=sf)
            self.assertEqual(man["status"], "failed"); self.assertIn("outage schedule failed", man["error"])
            self.assertTrue((Path(d) / "h1" / "events.jsonl").exists())   # raw log preserved


class MetricEdgeCases(unittest.TestCase):
    def test_recovery_lag_zero_when_backlog_already_covered(self):
        ev = [{"seq": 1, "commit_t": 0.0}]; cps = [{"terminal_event_sequence": 1, "confirmed_t": 2.0, "created_t": 1.0, "state": "confirmed", "seq": 1},
                                                   {"terminal_event_sequence": 1, "confirmed_t": 9.0, "created_t": 8.0, "state": "confirmed", "seq": 2}]
        self.assertEqual(m.recovery_lag(5.0, cps, ev), 0.0)

    def test_out_of_order_confirmation_uses_earliest_covering(self):
        ev = [{"seq": 1, "commit_t": 0.0}, {"seq": 2, "commit_t": 1.0}]
        cps = [{"terminal_event_sequence": 2, "confirmed_t": 5.0, "created_t": 3.0, "state": "confirmed", "seq": 2},
               {"terminal_event_sequence": 1, "confirmed_t": 9.0, "created_t": 2.0, "state": "confirmed", "seq": 1}]
        self.assertEqual(list(m.per_event_vulnerability(ev, cps)), [5.0, 4.0])   # cp2 (covers both) confirmed first

    def test_event_after_all_checkpoints_is_uncovered_not_zero(self):
        v = m.per_event_vulnerability([{"seq": 5, "commit_t": 1.0}], [{"terminal_event_sequence": 4, "confirmed_t": 2.0}])
        self.assertTrue(math.isnan(v[0]))

    def test_failed_only_checkpoints_give_no_protection(self):
        v = m.per_event_vulnerability([{"seq": 1, "commit_t": 0.0}], [{"terminal_event_sequence": 1, "confirmed_t": None, "state": "failed"}])
        self.assertTrue(math.isnan(v[0]))

    def test_all_requests_failed_latency_is_nan_not_zero(self):
        s = m.latency_summary([10, 20], [False, False]); self.assertEqual(s["success_rate"], 0.0); self.assertTrue(math.isnan(s["p95_ms"]))

    def test_max_age_before_first_confirmation_is_nan(self):
        self.assertTrue(math.isnan(m.max_confirmed_age_during([], 0, 5)))

    def test_bootstrap_ci_degenerate_inputs(self):
        self.assertTrue(all(math.isnan(x) for x in m.bootstrap_ci([1.0])))
        self.assertTrue(all(math.isnan(x) for x in m.bootstrap_ci([float("nan")] * 3)))

if __name__ == "__main__":
    unittest.main()
