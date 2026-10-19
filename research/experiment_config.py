"""Validated, frozen experiment configuration (Phase 4.1).

Implemented with stdlib dataclasses so it validates without extra dependencies.
Saved as canonical JSON; its SHA-256 digest goes into the run manifest.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path

POLICIES = {"A", "B", "C-risk", "C-unit"}
WORKLOADS = {"steady", "poisson", "bursty"}
AUDIT_MODES = {"full", "suffix"}            # mirrors Person 1's verifier modes (full-chain / suffix)
CHAIN_SCOPES = {"global"}                    # guide: single global chain; extend if agreed
_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$")


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class BurstSettings:
    high_rate: float = 0.0          # req/s during burst
    low_rate: float = 0.0           # req/s between bursts
    high_duration_s: float = 0.0
    low_duration_s: float = 0.0


@dataclass(frozen=True)
class OutageWindow:
    start_s: float                  # PLANNED offsets from run start (observed times logged separately)
    end_s: float
    mode: str = "down"              # down | delay | drop_response
    delay_seconds: float = 0.0
    label: str = ""


@dataclass(frozen=True)
class ExperimentConfig:
    run_id: str
    seed: int
    duration_s: float
    warmup_s: float
    policy_name: str
    policy_params: dict
    workload_type: str
    target_rate: float                       # requests / second
    risk_mixture: dict                       # {"low":..,"medium":..,"high":..} sums to 1
    action_mixture: dict = field(default_factory=lambda: {"create_patient": 0.3,
                                                          "update_patient": 0.3,
                                                          "create_appointment": 0.4})
    burst: BurstSettings = field(default_factory=BurstSettings)
    outages: tuple = ()
    audit_mode: str = "full"
    chain_scope: str = "global"
    n_clients: int = 1
    dispersion_window_s: float = 1.0         # observation window for I_D, declared up-front
    rate_tolerance: float = 0.10             # allowed relative error of achieved vs target rate
    delay_threshold_s: float = 5.0           # predeclared "delayed checkpoint" threshold

    def __post_init__(self):
        e = []
        if not _RUN_ID.match(self.run_id or ""): e.append("run_id invalid")
        if not isinstance(self.seed, int) or isinstance(self.seed, bool) or self.seed < 0:
            e.append("seed must be a non-negative integer")
        if self.duration_s <= 0: e.append("duration_s must be > 0")
        if self.warmup_s < 0 or self.warmup_s >= self.duration_s: e.append("warmup_s must be in [0, duration_s)")
        if self.policy_name not in POLICIES: e.append(f"policy_name must be in {sorted(POLICIES)}")
        if self.workload_type not in WORKLOADS: e.append(f"workload_type must be in {sorted(WORKLOADS)}")
        if self.target_rate <= 0: e.append("target_rate must be > 0")
        if self.n_clients < 1: e.append("n_clients must be >= 1")
        if self.audit_mode not in AUDIT_MODES: e.append(f"audit_mode must be in {sorted(AUDIT_MODES)}")
        if self.chain_scope not in CHAIN_SCOPES: e.append(f"chain_scope must be in {sorted(CHAIN_SCOPES)}")
        if self.dispersion_window_s <= 0: e.append("dispersion_window_s must be > 0")
        if not 0 < self.rate_tolerance < 1: e.append("rate_tolerance must be in (0,1)")
        for name, mix in (("risk_mixture", self.risk_mixture), ("action_mixture", self.action_mixture)):
            if not mix or any(v < 0 for v in mix.values()) or abs(sum(mix.values()) - 1.0) > 1e-9:
                e.append(f"{name} must be non-negative and sum to 1")
        if set(self.risk_mixture) - {"low", "medium", "high"}:
            e.append("risk_mixture keys must be low/medium/high")
        if self.workload_type == "bursty":
            b = self.burst
            if min(b.high_rate, b.low_rate) < 0 or b.high_rate <= 0 or b.high_duration_s <= 0 or b.low_duration_s <= 0:
                e.append("bursty workload needs burst.high_rate>0, low_rate>=0 and positive durations")
        prev_end = -1.0
        for o in sorted(self.outages, key=lambda w: w.start_s):
            if o.start_s < 0 or o.end_s <= o.start_s or o.end_s > self.duration_s:
                e.append(f"outage {o.label or o.start_s} outside [0,duration_s] or end<=start")
            if o.start_s < prev_end: e.append("outage windows overlap")
            if o.mode not in {"down", "delay", "drop_response"}: e.append("outage mode invalid")
            prev_end = max(prev_end, o.end_s)
        if e:
            raise ConfigError("; ".join(e))

    # -- (de)serialisation ----------------------------------------------------
    def to_dict(self) -> dict:
        d = asdict(self)
        d["outages"] = [asdict(o) for o in self.outages]
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "ExperimentConfig":
        d = dict(d)
        d["burst"] = BurstSettings(**d.get("burst", {}))
        d["outages"] = tuple(OutageWindow(**o) for o in d.get("outages", []))
        known = set(cls.__dataclass_fields__)
        unknown = set(d) - known
        if unknown: raise ConfigError(f"unknown config keys: {sorted(unknown)}")
        try:
            return cls(**d)
        except TypeError as exc:
            raise ConfigError(str(exc))

    def canonical_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"), allow_nan=False)

    def digest(self) -> str:
        return hashlib.sha256(self.canonical_json().encode()).hexdigest()

    def save(self, path) -> str:
        p = Path(path); p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(self.to_dict(), sort_keys=True, indent=2) + "\n")
        return self.digest()

    @classmethod
    def load(cls, path) -> "ExperimentConfig":
        return cls.from_dict(json.loads(Path(path).read_text()))
