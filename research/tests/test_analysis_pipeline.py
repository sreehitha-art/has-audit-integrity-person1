import json, tempfile, unittest, shutil
from pathlib import Path
from research.telemetry import Telemetry
from research.analysis.analyze_runs import analyze_all, analyze_run, public
from research.analysis.dashboard import build_dashboard


def synth_run(root, run_id="syn"):
    """Synthetic telemetry with known answers. Uses the SAME event names Person 1 will emit."""
    import time
    t = Telemetry(run_id, root); t.emit("experiment_start", seed=1)
    base = time.monotonic_ns()
    def at(sec, typ, **f):                       # inject controlled monotonic time
        rec = t.emit(typ, **f); return rec
    # control time by rewriting mono_ns afterwards
    t.close()
    p = Path(root) / run_id / "events.jsonl"; lines = [json.loads(l) for l in p.read_text().splitlines()]
    t0 = lines[0]["mono_ns"]
    def rec(s, typ, **f): return {"event_type": typ, "ts_utc": "x", "mono_ns": t0 + int(s * 1e9), "run_id": run_id, **f}
    extra = [rec(0.0, "event_committed", event_seq=1), rec(1.0, "event_committed", event_seq=2),
             rec(2.0, "checkpoint_created", checkpoint_seq=1, terminal_event_seq=2),
             rec(3.0, "checkpoint_confirmed", checkpoint_seq=1),
             rec(4.0, "event_committed", event_seq=3),
             rec(5.0, "outage_start", label="o", mode="down", planned_start_s=4.5, planned_end_s=8),
             rec(6.0, "checkpoint_created", checkpoint_seq=2, terminal_event_seq=3),
             rec(8.0, "outage_end", label="o", mode="down", planned_start_s=4.5, planned_end_s=8, observed_duration_s=3.0),
             rec(9.0, "checkpoint_confirmed", checkpoint_seq=2)]
    with open(p, "a") as f:
        for r in extra: f.write(json.dumps(r) + "\n")
    (Path(root) / run_id / "manifest.json").write_text(json.dumps({"status": "completed", "mode": "real", "seed": 1,
                                                                   "config_sha256": "ab" * 32}))


class PipelineTests(unittest.TestCase):
    def test_known_values_from_raw_events(self):
        with tempfile.TemporaryDirectory() as d:
            synth_run(d); r = analyze_run(Path(d) / "syn")
        v = r["vulnerability_s"]
        self.assertEqual(v["n_events"], 3); self.assertEqual(v["n_uncovered"], 0)
        self.assertAlmostEqual(v["mean"], (3.0 + 2.0 + 5.0) / 3)     # confirmed-time based: 3-0, 3-1, 9-4
        self.assertEqual(r["checkpoint_counts"], {"requested": 0, "pending": 0, "confirmed": 2, "failed": 0})
        o = r["outages"][0]
        self.assertAlmostEqual(o["observed_start_s"], 5.0); self.assertAlmostEqual(o["recovery_lag_s"], 1.0)
        self.assertAlmostEqual(o["max_confirmed_age_s"], 5.0, delta=0.1)     # cp1 confirmed t=3 -> age 5 at t=8

    def test_run_without_checkpoint_events_reports_null_not_fabricated(self):
        with tempfile.TemporaryDirectory() as d:
            t = Telemetry("m", d); t.emit("experiment_start"); t.emit("request_done", latency_ms=5.0, ok=True, in_warmup=False); t.close()
            r = analyze_run(Path(d) / "m")
        self.assertIsNone(r["vulnerability_s"]); self.assertIsNone(r["checkpoint_counts"])

    def test_corrupt_line_counted(self):
        with tempfile.TemporaryDirectory() as d:
            synth_run(d)
            with open(Path(d) / "syn" / "events.jsonl", "a") as f: f.write("{not json\n")
            self.assertEqual(analyze_run(Path(d) / "syn")["n_corrupt_lines"], 1)

    def test_regeneration_is_reproducible(self):
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as o1, tempfile.TemporaryDirectory() as o2:
            synth_run(d)
            for o in (o1, o2):
                res = analyze_all(Path(d), Path(o)); build_dashboard(res, Path(o))
            a = (Path(o1) / "syn.summary.json").read_text(); b = (Path(o2) / "syn.summary.json").read_text()
            self.assertEqual(a, b)
            self.assertEqual((Path(o1) / "index.html").read_text(), (Path(o2) / "index.html").read_text())
            self.assertTrue((Path(o1) / "syn.vulnerability.png").exists() and (Path(o1) / "syn.age.png").exists())
            self.assertIn("Checkpoint monitor", (Path(o1) / "index.html").read_text())

if __name__ == "__main__":
    unittest.main()
