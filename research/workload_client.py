"""Asynchronous workload client (Phase 4.3).

The workload logic is independent of the HMS transport. ``HmsClient`` is an interface:
  * ``MockHmsClient``  - development/unit tests ONLY. Results are labelled mock.
  * ``HttpxHmsClient`` - real adapter. Endpoint paths are PROVISIONAL (Person 1's API
    is not available yet) and are configured, not hard-wired. Needs ``httpx``.
Latency uses time.monotonic_ns(); failed/timed-out requests are never discarded.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Protocol

import numpy as np

from .experiment_config import ExperimentConfig
from .workload import PlannedRequest, plan_schedule


@dataclass
class HmsResult:
    status: int | None          # None => no HTTP response (timeout / connection error)
    error: str | None = None    # "timeout" | "connect_error" | ...


class HmsClient(Protocol):
    is_mock: bool
    async def send(self, req: PlannedRequest, payload: dict, idempotency_key: str) -> HmsResult: ...
    async def aclose(self) -> None: ...


class MockHmsClient:
    """Deterministic fake. NEVER treat outputs as real HMS experiment data."""
    is_mock = True

    def __init__(self, seed: int = 0, latency_s: float = 0.001, fail_every: int = 0,
                 timeout_every: int = 0, status_on_fail: int = 500):
        self.latency_s, self.fail_every, self.timeout_every = latency_s, fail_every, timeout_every
        self.status_on_fail = status_on_fail; self.calls = 0

    async def send(self, req, payload, idempotency_key):
        self.calls += 1
        await asyncio.sleep(self.latency_s)
        if self.timeout_every and req.index % self.timeout_every == self.timeout_every - 1:
            return HmsResult(None, "timeout")
        if self.fail_every and req.index % self.fail_every == self.fail_every - 1:
            return HmsResult(self.status_on_fail)
        return HmsResult(201)

    async def aclose(self): pass


class HttpxHmsClient:
    """REAL adapter (provisional paths). Auth token comes from the environment/arg,
    never from source code."""
    is_mock = False
    DEFAULT_PATHS = {"create_patient": ("POST", "/api/patients"),
                     "update_patient": ("PUT", "/api/patients/{id}"),
                     "create_appointment": ("POST", "/api/appointments")}

    def __init__(self, base_url: str, token: str | None = None, timeout_s: float = 10.0, paths=None):
        import httpx                      # lazy: only needed for real runs
        self._httpx = httpx
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        self._c = httpx.AsyncClient(base_url=base_url, headers=headers, timeout=timeout_s)
        self.paths = paths or self.DEFAULT_PATHS

    async def send(self, req, payload, idempotency_key):
        method, path = self.paths[req.action]
        try:
            r = await self._c.request(method, path.format(**payload.get("path", {"id": 1})),
                                      json=payload.get("body", payload),
                                      headers={"Idempotency-Key": idempotency_key})
            return HmsResult(r.status_code)
        except self._httpx.TimeoutException:
            return HmsResult(None, "timeout")
        except self._httpx.HTTPError as exc:
            return HmsResult(None, type(exc).__name__)

    async def aclose(self): await self._c.aclose()


def synthetic_payload(req: PlannedRequest, run_id: str) -> dict:
    """Synthetic data only. Risk class is carried so Person 1's policy can weight it
    (PROVISIONAL: how risk is derived in the HMS is Person 1's decision)."""
    return {"body": {"synthetic": True, "run_id": run_id, "request_index": req.index,
                     "action": req.action, "risk_class": req.risk_class,
                     "name": f"Synthetic Patient {req.index}"},
            "path": {"id": 1 + req.index % 50}}


def _pct(x, q): return float(np.percentile(x, q)) if len(x) else float("nan")


async def run_workload(cfg: ExperimentConfig, client: HmsClient, telemetry, *,
                       time_scale: float = 1.0, schedule: list[PlannedRequest] | None = None) -> dict:
    """Open-loop execution of the planned schedule across cfg.n_clients workers.
    ``time_scale`` < 1 compresses time (tests only; recorded in the summary)."""
    schedule = schedule if schedule is not None else plan_schedule(cfg)
    queue: asyncio.Queue = asyncio.Queue()
    for r in schedule: queue.put_nowait(r)
    results: list[dict] = []
    t0 = time.monotonic_ns()

    async def worker():
        while True:
            try: req = queue.get_nowait()
            except asyncio.QueueEmpty: return
            due = t0 + int(req.offset_s * time_scale * 1e9)
            wait = (due - time.monotonic_ns()) / 1e9
            if wait > 0: await asyncio.sleep(wait)
            sent = time.monotonic_ns()
            try:
                res = await client.send(req, synthetic_payload(req, cfg.run_id),
                                        f"{cfg.run_id}-req-{req.index}")
            except Exception as exc:                  # a crashing client is a failed request, not a lost one
                res = HmsResult(None, f"client_exception:{type(exc).__name__}")
            lat_ms = (time.monotonic_ns() - sent) / 1e6
            ok = res.status is not None and 200 <= res.status < 300
            rec = {"index": req.index, "planned_offset_s": req.offset_s, "action": req.action,
                   "risk_class": req.risk_class, "status": res.status, "error": res.error,
                   "latency_ms": lat_ms, "ok": ok,
                   "lateness_ms": (sent - due) / 1e6, "in_warmup": req.offset_s < cfg.warmup_s}
            results.append(rec)
            telemetry.emit("request_done", seed=cfg.seed, mock=client.is_mock, **rec)

    await asyncio.gather(*[worker() for _ in range(cfg.n_clients)])
    measured = [r for r in results if not r["in_warmup"]]
    lat_ok = [r["latency_ms"] for r in measured if r["ok"]]
    return {
        "run_id": cfg.run_id, "seed": cfg.seed, "mock_client": client.is_mock, "time_scale": time_scale,
        "n_planned": len(schedule), "n_sent": len(results), "n_measured": len(measured),
        "n_ok": sum(r["ok"] for r in measured),
        "n_timeouts": sum(r["error"] == "timeout" for r in measured),
        "n_failed": sum(not r["ok"] for r in measured),
        "success_rate": (sum(r["ok"] for r in measured) / len(measured)) if measured else float("nan"),
        "status_counts": {str(k): sum(r["status"] == k for r in measured)
                          for k in sorted({r["status"] for r in measured if r["status"] is not None})},
        "latency_ms_p50": _pct(lat_ok, 50), "latency_ms_p95": _pct(lat_ok, 95),
        "latency_ms_p99": _pct(lat_ok, 99),
    }
