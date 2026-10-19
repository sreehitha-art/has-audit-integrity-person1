"""Deterministic workload schedule generation (Phase 4.2).

All randomness (arrival gaps, actions, risk classes) comes from ONE generator,
``numpy.random.default_rng(seed)``, drawn in a fixed order, so identical seeds give
identical schedules. Policy-independent: the schedule never depends on system state.
"""
from __future__ import annotations

from dataclasses import dataclass
import numpy as np

from .experiment_config import ExperimentConfig


@dataclass(frozen=True)
class PlannedRequest:
    index: int
    offset_s: float        # planned send time relative to run start
    action: str
    risk_class: str


def _arrival_times(cfg: ExperimentConfig, rng: np.random.Generator) -> np.ndarray:
    T, rate = cfg.duration_s, cfg.target_rate
    if cfg.workload_type == "steady":
        n = int(np.floor(T * rate))
        return (np.arange(n) + 1) / rate if n else np.array([])
    if cfg.workload_type == "poisson":
        # exponential inter-arrival gaps with rate lambda; draw in chunks until T exceeded
        out, t = [], 0.0
        while t < T:
            gaps = rng.exponential(1.0 / rate, size=max(64, int(rate * T * 0.25)))
            ts = t + np.cumsum(gaps)
            out.append(ts); t = ts[-1]
        ts = np.concatenate(out)
        return ts[ts <= T]
    # bursty: alternating high/low-rate phases, Poisson within each phase
    b = cfg.burst
    times, t0, high = [], 0.0, True
    while t0 < T:
        dur = b.high_duration_s if high else b.low_duration_s
        r = b.high_rate if high else b.low_rate
        end = min(t0 + dur, T)
        if r > 0:
            t = t0
            while True:
                t += rng.exponential(1.0 / r)
                if t > end: break
                times.append(t)
        t0, high = end, not high
    return np.array(times)


def plan_schedule(cfg: ExperimentConfig) -> list[PlannedRequest]:
    rng = np.random.default_rng(cfg.seed)
    arrivals = _arrival_times(cfg, rng)
    n = len(arrivals)
    actions, ap = zip(*sorted(cfg.action_mixture.items()))
    risks, rp = zip(*sorted(cfg.risk_mixture.items()))
    a_idx = rng.choice(len(actions), size=n, p=np.array(ap) / sum(ap)) if n else []
    r_idx = rng.choice(len(risks), size=n, p=np.array(rp) / sum(rp)) if n else []
    return [PlannedRequest(i, float(arrivals[i]), actions[a_idx[i]], risks[r_idx[i]])
            for i in range(n)]


def schedule_digest(schedule: list[PlannedRequest]) -> str:
    import hashlib, json
    payload = json.dumps([[r.index, round(r.offset_s, 9), r.action, r.risk_class] for r in schedule],
                         separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def counts_per_window(offsets, duration_s: float, window_s: float) -> np.ndarray:
    n_win = int(np.floor(duration_s / window_s))
    if n_win < 1: raise ValueError("duration shorter than one dispersion window")
    edges = np.arange(n_win + 1) * window_s
    return np.histogram(offsets, bins=edges)[0]


def index_of_dispersion(offsets, duration_s: float, window_s: float) -> float:
    """I_D = Var(C)/E(C), C = request count per fixed window (sample variance, ddof=1)."""
    c = counts_per_window(offsets, duration_s, window_s)
    mean = c.mean()
    if mean == 0 or len(c) < 2: return float("nan")
    return float(c.var(ddof=1) / mean)


def schedule_stats(cfg: ExperimentConfig, schedule: list[PlannedRequest]) -> dict:
    offs = np.array([r.offset_s for r in schedule])
    achieved = len(schedule) / cfg.duration_s
    if cfg.workload_type == "bursty":
        b = cfg.burst
        cyc = b.high_duration_s + b.low_duration_s
        expected = (b.high_rate * b.high_duration_s + b.low_rate * b.low_duration_s) / cyc
    else:
        expected = cfg.target_rate
    rel_err = abs(achieved - expected) / expected if expected else float("nan")
    return {
        "n_planned": len(schedule), "duration_s": cfg.duration_s,
        "target_rate": cfg.target_rate, "expected_mean_rate": expected,
        "achieved_planned_rate": achieved, "relative_error": rel_err,
        "rate_tolerance": cfg.rate_tolerance, "within_tolerance": bool(rel_err <= cfg.rate_tolerance),
        "dispersion_window_s": cfg.dispersion_window_s,
        "index_of_dispersion": index_of_dispersion(offs, cfg.duration_s, cfg.dispersion_window_s),
        "schedule_digest": schedule_digest(schedule),
    }
