"""Outage controller: drives the anchor's test-only fault endpoint on a schedule and logs
PLANNED vs OBSERVED fault times separately."""
from __future__ import annotations
import time
from typing import Callable
from .experiment_config import OutageWindow
from .telemetry import Telemetry, utc_now_iso

SetFault = Callable[[str, float], None]     # (mode, delay_seconds) -> None; raises on failure


def http_set_fault(base_url: str, test_token: str) -> SetFault:
    import httpx
    def _set(mode, delay=0.0):
        r = httpx.post(f"{base_url}/v1/_test/fault", json={"mode": mode, "delay_seconds": delay},
                       headers={"X-Test-Token": test_token}, timeout=5.0)
        r.raise_for_status()
    return _set


def run_outage_schedule(outages: tuple[OutageWindow, ...], set_fault: SetFault, tel: Telemetry,
                        time_scale: float = 1.0, sleep=time.sleep, clock=time.monotonic) -> list[dict]:
    """Blocking. Run in a thread alongside the workload. Returns observed intervals."""
    t0 = clock(); observed = []
    for w in sorted(outages, key=lambda x: x.start_s):
        sleep(max(0.0, t0 + w.start_s * time_scale - clock()))
        s_utc, s_ns = utc_now_iso(), time.monotonic_ns()
        set_fault(w.mode, w.delay_seconds)
        tel.emit("outage_start", label=w.label, mode=w.mode, planned_start_s=w.start_s,
                 planned_end_s=w.end_s, observed_start_utc=s_utc, observed_start_mono_ns=s_ns)
        sleep(max(0.0, t0 + w.end_s * time_scale - clock()))
        set_fault("ok", 0.0)
        e_utc, e_ns = utc_now_iso(), time.monotonic_ns()
        tel.emit("outage_end", label=w.label, mode=w.mode, planned_start_s=w.start_s,
                 planned_end_s=w.end_s, observed_end_utc=e_utc, observed_end_mono_ns=e_ns,
                 observed_duration_s=(e_ns - s_ns) / 1e9)
        observed.append({"label": w.label, "mode": w.mode, "planned": [w.start_s, w.end_s],
                         "observed_start_mono_ns": s_ns, "observed_end_mono_ns": e_ns})
    return observed
