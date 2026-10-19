import dataclasses, json, tempfile, unittest
from collections import Counter
from pathlib import Path
from research.experiment_config import ExperimentConfig, ConfigError, BurstSettings, OutageWindow
from research.workload import plan_schedule, schedule_stats, schedule_digest, index_of_dispersion


def cfg(**kw):
    base = dict(run_id="r1", seed=42, duration_s=600, warmup_s=10, policy_name="B",
                policy_params={"N": 20}, workload_type="poisson", target_rate=20,
                risk_mixture={"low": 0.6, "medium": 0.3, "high": 0.1})
    base.update(kw); return ExperimentConfig(**base)


class ConfigTests(unittest.TestCase):
    def test_valid_and_digest_stable(self):
        a, b = cfg(), cfg()
        self.assertEqual(a.digest(), b.digest())
        self.assertNotEqual(a.digest(), cfg(seed=43).digest())

    def test_roundtrip_and_save(self):
        c = cfg(workload_type="bursty", burst=BurstSettings(50, 5, 5, 15),
                outages=(OutageWindow(100, 130, "down", label="o1"),))
        with tempfile.TemporaryDirectory() as d:
            dig = c.save(Path(d) / "c.json")
            c2 = ExperimentConfig.load(Path(d) / "c.json")
        self.assertEqual(dig, c2.digest()); self.assertEqual(c, c2)

    def test_invalid_configs(self):
        bad = [dict(seed=-1), dict(duration_s=0), dict(warmup_s=600), dict(policy_name="Z"),
               dict(workload_type="x"), dict(target_rate=0), dict(n_clients=0),
               dict(run_id="../x"), dict(risk_mixture={"low": 0.5, "medium": 0.1, "high": 0.1}),
               dict(risk_mixture={"low": 1.0, "weird": 0.0}),
               dict(workload_type="bursty"),
               dict(outages=(OutageWindow(10, 5),)),
               dict(outages=(OutageWindow(10, 50), OutageWindow(40, 80))),
               dict(outages=(OutageWindow(10, 9999),))]
        for kw in bad:
            with self.assertRaises(ConfigError, msg=str(kw)):
                cfg(**kw)

    def test_unknown_key_rejected(self):
        d = cfg().to_dict(); d["bogus"] = 1
        with self.assertRaises(ConfigError): ExperimentConfig.from_dict(d)


class WorkloadTests(unittest.TestCase):
    def test_same_seed_same_schedule_diff_seed_differs(self):
        for wt, extra in [("steady", {}), ("poisson", {}),
                          ("bursty", dict(burst=BurstSettings(60, 5, 5, 15)))]:
            a = plan_schedule(cfg(workload_type=wt, **extra))
            b = plan_schedule(cfg(workload_type=wt, **extra))
            c = plan_schedule(cfg(workload_type=wt, seed=7, **extra))
            self.assertEqual(a, b, wt)
            self.assertEqual(schedule_digest(a), schedule_digest(b))
            if wt != "steady":   # steady arrivals are deterministic; actions/risks still seeded
                self.assertNotEqual(schedule_digest(a), schedule_digest(c), wt)
        s1 = plan_schedule(cfg(workload_type="steady")); s2 = plan_schedule(cfg(workload_type="steady", seed=7))
        self.assertNotEqual([(r.action, r.risk_class) for r in s1], [(r.action, r.risk_class) for r in s2])

    def test_steady_fixed_intervals(self):
        s = plan_schedule(cfg(workload_type="steady", target_rate=10, duration_s=20, warmup_s=1))
        gaps = {round(b.offset_s - a.offset_s, 9) for a, b in zip(s, s[1:])}
        self.assertEqual(gaps, {0.1}); self.assertEqual(len(s), 200)

    def test_offsets_sorted_within_duration(self):
        for wt, extra in [("poisson", {}), ("bursty", dict(burst=BurstSettings(60, 5, 5, 15)))]:
            c = cfg(workload_type=wt, **extra); o = [r.offset_s for r in plan_schedule(c)]
            self.assertEqual(o, sorted(o)); self.assertTrue(0 < min(o) and max(o) <= c.duration_s)

    def test_achieved_rate_within_tolerance_and_reported(self):
        for wt, extra in [("steady", {}), ("poisson", {}),
                          ("bursty", dict(burst=BurstSettings(60, 5, 5, 15)))]:
            c = cfg(workload_type=wt, duration_s=1200, **extra)
            st = schedule_stats(c, plan_schedule(c))
            self.assertTrue(st["within_tolerance"], (wt, st))
            for k in ("achieved_planned_rate", "index_of_dispersion", "schedule_digest"):
                self.assertIn(k, st)

    def test_index_of_dispersion_ordering(self):
        d = {}
        for wt, extra in [("steady", {}), ("poisson", {}),
                          ("bursty", dict(burst=BurstSettings(60, 5, 5, 15)))]:
            c = cfg(workload_type=wt, duration_s=1200, **extra)
            d[wt] = schedule_stats(c, plan_schedule(c))["index_of_dispersion"]
        self.assertLess(d["steady"], 0.1)           # near-deterministic arrivals
        self.assertAlmostEqual(d["poisson"], 1.0, delta=0.25)   # Poisson ~1
        self.assertGreater(d["bursty"], 2.0)        # overdispersed

    def test_mixtures_roughly_respected(self):
        s = plan_schedule(cfg(duration_s=1200)); cnt = Counter(r.risk_class for r in s)
        self.assertAlmostEqual(cnt["low"] / len(s), 0.6, delta=0.03)
        self.assertAlmostEqual(cnt["high"] / len(s), 0.1, delta=0.02)

if __name__ == "__main__":
    unittest.main()
