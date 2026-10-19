"""Exposure and evaluation metrics (Phase 7.1 / 7.3). Pure functions over event records.

Inputs are plain dicts derived from telemetry:
  events:      [{"seq": int, "commit_t": float_seconds}]           (event_committed)
  checkpoints: [{"seq": int, "terminal_event_sequence": int,
                 "created_t": float|None, "confirmed_t": float|None, "state": str}]
All times are seconds on ONE monotonic timeline. Protection is measured from
``confirmed_t`` (independent-anchor confirmation), NEVER from ``created_t``.
"""
from __future__ import annotations

import numpy as np

STATES = ("requested", "pending", "confirmed", "failed")


def per_event_vulnerability(events, checkpoints, horizon_t=None):
    """V_i = (confirmed_t of FIRST confirmed checkpoint with terminal_event_sequence >= seq_i) - commit_t_i.
    Events never covered by a confirmed checkpoint get NaN (censored); count them via
    ``uncovered_count``. Because checkpoints cover a prefix and are published in order,
    'first covering' = smallest confirmed_t among covering checkpoints."""
    conf = sorted((c for c in checkpoints if c.get("confirmed_t") is not None),
                  key=lambda c: c["terminal_event_sequence"])
    out = []
    for e in events:
        times = [c["confirmed_t"] for c in conf if c["terminal_event_sequence"] >= e["seq"]]
        out.append(min(times) - e["commit_t"] if times else float("nan"))
    return np.array(out, dtype=float)


def uncovered_count(events, checkpoints) -> int:
    return int(np.isnan(per_event_vulnerability(events, checkpoints)).sum())


def unanchored_suffix_age(events, checkpoints, at_t: float) -> float:
    """Age at time ``at_t`` of the oldest committed event not covered by a checkpoint
    confirmed by ``at_t``. 0.0 if none."""
    covered_upto = max((c["terminal_event_sequence"] for c in checkpoints
                        if c.get("confirmed_t") is not None and c["confirmed_t"] <= at_t), default=0)
    pend = [at_t - e["commit_t"] for e in events if e["commit_t"] <= at_t and e["seq"] > covered_upto]
    return max(pend) if pend else 0.0


def checkpoint_counts(checkpoints) -> dict:
    d = {s: 0 for s in STATES}
    for c in checkpoints:
        if c["state"] in d: d[c["state"]] += 1
    return d


def latency_summary(latencies_ms, statuses_ok) -> dict:
    lat = np.asarray(latencies_ms, dtype=float)
    ok = np.asarray(statuses_ok, dtype=bool)
    okl = lat[ok]
    q = (lambda p: float(np.percentile(okl, p))) if len(okl) else (lambda p: float("nan"))
    return {"n": int(len(lat)), "success_rate": float(ok.mean()) if len(lat) else float("nan"),
            "p50_ms": q(50), "p95_ms": q(95), "p99_ms": q(99)}


def confirmed_age_series(checkpoints, t_grid):
    """Age of the latest confirmed checkpoint at each grid time (NaN before the first)."""
    conf = sorted(c["confirmed_t"] for c in checkpoints if c.get("confirmed_t") is not None)
    out = []
    for t in t_grid:
        prior = [x for x in conf if x <= t]
        out.append(t - prior[-1] if prior else float("nan"))
    return np.array(out)


def max_confirmed_age_during(checkpoints, start_t, end_t, step=0.05) -> float:
    """Max age of the latest confirmed checkpoint in [start_t, end_t]. Instants before the FIRST
    confirmation have undefined age and are skipped (NaN if the whole window is such)."""
    grid = np.arange(start_t, end_t + step, step)
    a = confirmed_age_series(checkpoints, grid)
    return float(np.nanmax(a)) if np.isfinite(a).any() else float("nan")


def recovery_lag(outage_end_t, checkpoints, events=None) -> float:
    """Time from outage end until the backlog is cleared: the first confirmation at/after
    outage_end_t of a checkpoint that covers every event committed up to outage_end_t.
    NaN if never cleared (censored)."""
    last_seq = max((e["seq"] for e in events if e["commit_t"] <= outage_end_t), default=0) if events else 0
    if events and any(c.get("confirmed_t") is not None and c["confirmed_t"] <= outage_end_t
                      and c["terminal_event_sequence"] >= last_seq for c in checkpoints):
        return 0.0      # backlog already covered at/before outage end (e.g. a 'delay' fault): no recovery needed
    cand = [c["confirmed_t"] for c in checkpoints
            if c.get("confirmed_t") is not None and c["confirmed_t"] >= outage_end_t
            and c["terminal_event_sequence"] >= last_seq]
    return min(cand) - outage_end_t if cand else float("nan")


def delayed_and_failed(checkpoints, delay_threshold_s: float) -> dict:
    delayed = [c for c in checkpoints if c.get("confirmed_t") is not None and c.get("created_t") is not None
               and c["confirmed_t"] - c["created_t"] > delay_threshold_s]
    return {"failed": sum(c["state"] == "failed" for c in checkpoints), "delayed": len(delayed),
            "delay_threshold_s": delay_threshold_s}


# ---- prediction-accuracy metrics (no MAPE) --------------------------------
def _pair(pred, obs):
    p, o = np.asarray(pred, float), np.asarray(obs, float)
    m = np.isfinite(p) & np.isfinite(o)
    return p[m], o[m]

def mae(pred, obs):
    p, o = _pair(pred, obs); return float(np.mean(np.abs(p - o)))
def rmse(pred, obs):
    p, o = _pair(pred, obs); return float(np.sqrt(np.mean((p - o) ** 2)))
def bias(pred, obs):
    """mean(pred - obs): positive = over-prediction."""
    p, o = _pair(pred, obs); return float(np.mean(p - o))
def nmae(pred, obs):
    p, o = _pair(pred, obs); m = np.mean(o)
    return float(np.mean(np.abs(p - o)) / m) if m > 0 else float("nan")


def paired_difference(a, b):
    """Per-pair differences a-b for runs matched on the same seed/event stream."""
    a, b = np.asarray(a, float), np.asarray(b, float)
    if a.shape != b.shape: raise ValueError("paired samples must have equal length")
    return a - b


def bootstrap_ci(values, stat=np.mean, n_boot=10_000, alpha=0.05, seed=0):
    """Percentile bootstrap CI (seeded => reproducible)."""
    v = np.asarray(values, float); v = v[np.isfinite(v)]
    if len(v) < 2: return (float("nan"), float("nan"))
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(v), size=(n_boot, len(v)))
    s = np.apply_along_axis(stat, 1, v[idx])
    return (float(np.quantile(s, alpha / 2)), float(np.quantile(s, 1 - alpha / 2)))
