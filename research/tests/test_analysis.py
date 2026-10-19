import math, unittest
import numpy as np
from research.analysis import metrics as m, models as md


EVENTS = [{"seq": 1, "commit_t": 0.0}, {"seq": 2, "commit_t": 1.0}, {"seq": 3, "commit_t": 4.0},
          {"seq": 4, "commit_t": 9.0}]
CPS = [{"seq": 1, "terminal_event_sequence": 2, "created_t": 2.0, "confirmed_t": 3.0, "state": "confirmed"},
       {"seq": 2, "terminal_event_sequence": 3, "created_t": 5.0, "confirmed_t": 12.0, "state": "confirmed"},
       {"seq": 3, "terminal_event_sequence": 4, "created_t": 10.0, "confirmed_t": None, "state": "pending"},
       {"seq": 4, "terminal_event_sequence": 4, "created_t": 11.0, "confirmed_t": None, "state": "failed"}]


class MetricTests(unittest.TestCase):
    def test_vulnerability_uses_confirmation_not_creation(self):
        v = m.per_event_vulnerability(EVENTS, CPS)
        self.assertEqual(list(v[:3]), [3.0, 2.0, 8.0])      # 3-0, 3-1, 12-4 (NOT created_t 5)
        self.assertTrue(math.isnan(v[3])); self.assertEqual(m.uncovered_count(EVENTS, CPS), 1)

    def test_suffix_age(self):
        self.assertEqual(m.unanchored_suffix_age(EVENTS, CPS, at_t=2.5), 2.5)    # nothing confirmed yet
        self.assertEqual(m.unanchored_suffix_age(EVENTS, CPS, at_t=5.0), 1.0)    # seq3 (t=4) uncovered
        self.assertEqual(m.unanchored_suffix_age(EVENTS, CPS, at_t=12.5), 3.5)   # seq4 (t=9) uncovered
        self.assertEqual(m.unanchored_suffix_age([], [], 5), 0.0)

    def test_counts_latency(self):
        self.assertEqual(m.checkpoint_counts(CPS), {"requested": 0, "pending": 1, "confirmed": 2, "failed": 1})
        s = m.latency_summary([10, 20, 30, 40, 1000], [1, 1, 1, 1, 0])
        self.assertEqual(s["success_rate"], 0.8); self.assertAlmostEqual(s["p50_ms"], 25.0)

    def test_max_age_and_recovery(self):
        # latest confirmed checkpoint is cp1 (t=3) until cp2 confirms at t=12 -> age approaches 9
        self.assertAlmostEqual(m.max_confirmed_age_during(CPS, 3.0, 11.99, step=0.01), 8.99, places=1)
        # outage ends at t=6; backlog = events up to seq3 (commit<=6); covered at t=12 -> lag 6
        self.assertEqual(m.recovery_lag(6.0, CPS, EVENTS), 6.0)
        self.assertTrue(math.isnan(m.recovery_lag(13.0, CPS, EVENTS)))

    def test_delayed_failed(self):
        d = m.delayed_and_failed(CPS, delay_threshold_s=5.0)
        self.assertEqual((d["failed"], d["delayed"]), (1, 1))     # cp2 took 7s

    def test_error_metrics_known_values(self):
        p, o = [2, 4, 6], [1, 5, 9]
        self.assertAlmostEqual(m.mae(p, o), (1 + 1 + 3) / 3)
        self.assertAlmostEqual(m.rmse(p, o), math.sqrt((1 + 1 + 9) / 3))
        self.assertAlmostEqual(m.bias(p, o), (1 - 1 - 3) / 3)
        self.assertAlmostEqual(m.nmae(p, o), (5 / 3) / 5)
        self.assertEqual(m.mae([1, float("nan")], [2, 3]), 1.0)     # NaN pairs dropped

    def test_paired_and_bootstrap(self):
        d = m.paired_difference([5, 6, 7, 8], [4, 4, 4, 4]); self.assertEqual(list(d), [1, 2, 3, 4])
        with self.assertRaises(ValueError): m.paired_difference([1], [1, 2])
        lo, hi = m.bootstrap_ci(d, seed=1); self.assertLess(lo, 2.5); self.assertGreater(hi, 2.5)
        self.assertEqual(m.bootstrap_ci(d, seed=1), m.bootstrap_ci(d, seed=1))   # reproducible


class ModelValidationTests(unittest.TestCase):
    def test_policy_b_closed_forms_match_simulation(self):
        for N, lam in [(5, 2.0), (20, 10.0), (50, 1.0)]:
            sim = md.simulate_policy_b(N, lam, seed=N)
            self.assertAlmostEqual(sim["V"], md.predict_v_policy_b(N, lam), delta=0.03 * md.predict_v_policy_b(N, lam) + 0.01)
            self.assertAlmostEqual(sim["A"], md.predict_a_policy_b(N, lam), delta=0.03 * md.predict_a_policy_b(N, lam))

    def test_policy_a_matches_simulation_when_rate_high(self):
        for T, lam, D in [(10, 5.0, 0.0), (10, 5.0, 1.5), (30, 2.0, 0.5)]:
            sim = md.simulate_policy_a(T, lam, D, seed=T)
            self.assertAlmostEqual(sim, md.predict_v_policy_a(T, D), delta=0.05 * (T / 2 + D))

    def test_policy_a_low_rate_is_outside_model_assumptions(self):
        # With lambda*T << 1 most intervals are empty; a checkpoint needs >=1 eligible event, so
        # real checkpoint spacing exceeds T and T/2 + E[D] under-predicts. Demonstrate with a
        # simulator that skips empty intervals (the guide's trigger rule).
        rng = np.random.default_rng(5); T, lam, horizon = 10.0, 0.02, 400_000.0
        ev = np.sort(rng.uniform(0, horizon, rng.poisson(lam * horizon)))
        ticks = np.arange(T, horizon + T, T)
        v, last_tick_idx = [], -1
        for e in ev:                                   # event waits for the first tick >= e
            v.append(ticks[np.searchsorted(ticks, e, side="left")] - e)
        # events are covered at their own interval's tick here, so V stays ~T/2 ...
        self.assertAlmostEqual(float(np.mean(v)), T / 2, delta=0.3)
        # ... but the checkpoint COUNT is far below horizon/T, so A_A = G/2 with G=T is wrong:
        n_cp = len(set(np.searchsorted(ticks, ev, side="left")))
        self.assertLess(n_cp, 0.25 * (horizon / T))
    def test_renewal_age_formula(self):
        self.assertEqual(md.predict_a_policy_a([10] * 5), 5.0)            # deterministic gaps -> G/2
        rng = np.random.default_rng(0); g = rng.exponential(10, 200_000)
        self.assertAlmostEqual(md.predict_a_policy_a(g), 10.0, delta=0.3)  # exponential -> mean

    def test_monte_carlo_c_reduces_to_known_cases(self):
        w = {"low": 1, "medium": 2, "high": 4}
        # all-weight-1 events, no cap effect -> same as policy B with N=R
        r = md.monte_carlo_policy_c(R=10, T_max=1e9, lam=5.0, risk_weights=w,
                                    risk_probs={"low": 1.0, "medium": 0.0, "high": 0.0}, n_sims=4000, seed=1)
        self.assertAlmostEqual(r["mean_vulnerability_s"], md.predict_v_policy_b(10, 5.0), delta=0.05)
        self.assertEqual(r["cap_fraction"], 0.0)
        # tiny cap dominates
        r2 = md.monte_carlo_policy_c(R=1e9, T_max=2.0, lam=5.0, risk_weights=w,
                                     risk_probs={"low": 1.0, "medium": 0.0, "high": 0.0}, n_sims=2000, seed=1)
        self.assertGreater(r2["cap_fraction"], 0.95)
        self.assertLess(r2["mean_cycle_s"], 2.5)
        # reproducible
        r3 = md.monte_carlo_policy_c(R=10, T_max=1e9, lam=5.0, risk_weights=w,
                                     risk_probs={"low": 1.0, "medium": 0.0, "high": 0.0}, n_sims=4000, seed=1)
        self.assertEqual(r, r3)

if __name__ == "__main__":
    unittest.main()
