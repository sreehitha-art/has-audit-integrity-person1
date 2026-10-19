import json, tempfile, threading, unittest
from datetime import datetime
from pathlib import Path
from research import telemetry
from research.telemetry import Telemetry, utc_now_iso


class TelemetryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        telemetry.end_run()
        self.tmp.cleanup()

    def _lines(self, run_id):
        return (self.root / run_id / "events.jsonl").read_text().splitlines()

    def test_concurrent_writes_are_valid_jsonl(self):
        t = Telemetry("run-conc", self.root)
        def worker(i):
            for j in range(200):
                t.emit("request_done", worker=i, seq=j, payload="x" * 300)
        ths = [threading.Thread(target=worker, args=(i,)) for i in range(16)]
        [x.start() for x in ths]; [x.join() for x in ths]
        t.close()
        lines = self._lines("run-conc")
        self.assertEqual(len(lines), 16 * 200)
        seen = set()
        for ln in lines:
            rec = json.loads(ln)
            self.assertEqual(rec["payload"], "x" * 300)
            seen.add((rec["worker"], rec["seq"]))
        self.assertEqual(len(seen), 16 * 200)

    def test_timestamps(self):
        t = Telemetry("run-ts", self.root)
        a = t.emit("outage_start"); b = t.emit("outage_end"); t.close()
        self.assertTrue(a["ts_utc"].endswith("Z"))
        datetime.strptime(a["ts_utc"], "%Y-%m-%dT%H:%M:%S.%fZ")
        self.assertIsInstance(a["mono_ns"], int)
        self.assertGreaterEqual(b["mono_ns"], a["mono_ns"])
        self.assertTrue(utc_now_iso().endswith("Z"))

    def test_same_run_id_writes_expected_location(self):
        t = telemetry.start_run("run-loc", self.root)
        telemetry.emit("experiment_start", seed=1)
        telemetry.end_run()
        self.assertEqual(t.path, self.root / "run-loc" / "events.jsonl")
        self.assertTrue(t.path.exists())
        telemetry.start_run("run-loc", self.root)   # restart appends, never truncates
        telemetry.emit("experiment_end"); telemetry.end_run()
        self.assertEqual(len(self._lines("run-loc")), 2)

    def test_raw_log_survives_failed_experiment(self):
        try:
            with Telemetry("run-fail", self.root) as t:
                t.emit("experiment_start")
                raise RuntimeError("experiment crashed")
        except RuntimeError:
            pass
        lines = self._lines("run-fail")
        self.assertEqual(json.loads(lines[0])["event_type"], "experiment_start")

    def test_serialization_error_does_not_raise_or_corrupt(self):
        t = Telemetry("run-ser", self.root)
        self.assertIsNone(t.emit("x", bad=float("nan")))
        self.assertIsNone(t.emit("x", bad=object()))
        t.emit("ok"); t.close()
        self.assertEqual(t.dropped, 2)
        self.assertEqual(len(self._lines("run-ser")), 1)
        with self.assertRaises(ValueError):
            Telemetry("s", self.root, strict=True).emit("x", bad=float("inf"))

    def test_invalid_run_id_rejected(self):
        for bad in ["../evil", "a/b", "", ".hidden", "x" * 101]:
            with self.assertRaises(ValueError):
                Telemetry(bad, self.root)

    def test_emit_without_active_run_is_noop(self):
        telemetry.end_run()
        self.assertIsNone(telemetry.emit("event_committed"))

if __name__ == "__main__":
    unittest.main()
