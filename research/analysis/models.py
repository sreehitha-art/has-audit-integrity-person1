"""Candidate exposure-prediction models (Phase 7.2).

Every formula states its assumptions. They are CANDIDATES to be validated against
simulation (see tests) before use on the full system; nothing here is assumed correct.

Definitions: V = per-event vulnerability (event commit -> confirmation of first covering
checkpoint); A = time-average age of the latest confirmed checkpoint.
"""
from __future__ import annotations

import numpy as np


def predict_v_policy_a(T: float, mean_delay: float = 0.0) -> float:
    """V_A ~= T/2 + E[D].
    Assumes: events arrive uniformly within the interval (e.g. Poisson/steady, with
    non-trivial rate), a checkpoint fires every T, confirmation delay D is independent of
    arrival with mean E[D], and no-event intervals do not skip checkpoints (guide: a
    checkpoint needs >=1 eligible event, so at very low rates the model is NOT applicable)."""
    return T / 2.0 + mean_delay


def predict_a_policy_a(gaps) -> float:
    """A_A = E[G^2] / (2 E[G]) for renewal process with inter-confirmation gaps G."""
    g = np.asarray(gaps, float)
    return float(np.mean(g ** 2) / (2 * np.mean(g)))


def predict_v_policy_b(N: int, lam: float) -> float:
    """V_B = (N-1)/(2*lam). Assumes Poisson(lam) arrivals, checkpoint at every N-th event
    with ZERO confirmation delay and no outages. Mean over the N events of
    (time to the N-th event) is (N-1)/(2*lam) for the within-batch waits."""
    return (N - 1) / (2.0 * lam)


def predict_a_policy_b(N: int, lam: float) -> float:
    """A_B = (N+1)/(2*lam). Same assumptions as predict_v_policy_b."""
    return (N + 1) / (2.0 * lam)


def monte_carlo_policy_c(R: float, T_max: float, lam: float, risk_weights: dict, risk_probs: dict,
                         n_sims: int = 5000, seed: int = 0, mean_delay: float = 0.0) -> dict:
    """Monte Carlo for C-risk: Poisson(lam) arrivals, i.i.d. risk classes; a checkpoint fires when
    accumulated weight >= R or T_max has elapsed since the cycle start (with >=1 event pending;
    if the cap passes with no events, the trigger waits for the first event).
    Returns mean cycle length, mean per-event vulnerability, mean confirmed-age and the
    fraction of cycles ended by the time cap. Zero-delay unless mean_delay given (constant)."""
    rng = np.random.default_rng(seed)
    classes = sorted(risk_probs); probs = np.array([risk_probs[c] for c in classes], float)
    w = np.array([risk_weights[c] for c in classes], float)
    cycle, v_means, v_weights, capped = [], [], [], 0
    for _ in range(n_sims):
        t, risk, times = 0.0, 0.0, []
        while True:
            t += rng.exponential(1.0 / lam)
            if t > T_max and times:         # cap reached before this arrival
                end, was_cap = T_max, True; break
            times.append(t); risk += w[rng.choice(len(w), p=probs)]
            if risk >= R:
                end, was_cap = t, False; break
            if t >= T_max:
                end, was_cap = t, True; break
        capped += was_cap
        cycle.append(end)
        v = end - np.array(times) + mean_delay
        v_means.append(v.sum()); v_weights.append(len(v))
    cyc = np.array(cycle)
    return {"mean_cycle_s": float(cyc.mean()),
            "mean_vulnerability_s": float(np.sum(v_means) / np.sum(v_weights)),
            "mean_age_s": float(np.mean(cyc ** 2) / (2 * np.mean(cyc)) + mean_delay),
            "cap_fraction": capped / n_sims, "n_sims": n_sims, "seed": seed}


# ---- controlled simulations used to VALIDATE the closed forms -------------------
def simulate_policy_b(N: int, lam: float, n_events: int = 200_000, seed: int = 0) -> dict:
    rng = np.random.default_rng(seed)
    t = np.cumsum(rng.exponential(1 / lam, n_events))
    n_batches = n_events // N
    t = t[: n_batches * N].reshape(n_batches, N)
    v = t[:, -1:] - t                      # commit -> checkpoint (zero delay)
    cyc = np.diff(np.concatenate([[0.0], t[:, -1]]))
    return {"V": float(v.mean()), "A": float(np.mean(cyc ** 2) / (2 * np.mean(cyc)))}


def simulate_policy_a(T: float, lam: float, mean_delay: float = 0.0, horizon: float = 20_000,
                      seed: int = 0) -> float:
    """Per-event V with checkpoints every T (only when >=1 event pending) and constant delay."""
    rng = np.random.default_rng(seed)
    n = rng.poisson(lam * horizon); ev = np.sort(rng.uniform(0, horizon, n))
    ticks = np.arange(T, horizon + T, T)
    idx = np.searchsorted(ticks, ev, side="left")     # first tick >= event
    return float(np.mean(ticks[idx] - ev + mean_delay))
