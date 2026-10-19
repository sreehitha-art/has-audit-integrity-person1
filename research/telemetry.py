"""Telemetry foundation (Phase 1).

One JSON object per line is appended to ``research/raw/<run_id>/events.jsonl``.

Design notes
------------
* Thread-safe: a process-wide lock serialises writes, and each record is written
  with a single ``write`` call followed by flush (+ fsync optional), so lines never
  interleave.
* Never raises into the caller by default: telemetry must not break a business
  transaction. Failures are counted in ``Telemetry.dropped`` and reported on stderr.
* Call it AFTER database commits, never inside a transaction.
* Raw logs are never deleted by this module, so they survive failed experiments.
"""
from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_RAW_ROOT = Path(__file__).resolve().parent / "raw"
_RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$")

# Event types Person 2 emits (Person 1's are documented in docs/telemetry_interface.md).
P2_EVENT_TYPES = {
    "experiment_start", "experiment_end", "request_done",
    "outage_start", "outage_end", "tamper_applied",
    "anchor_fault_start", "anchor_fault_end",
}
P1_EVENT_TYPES = {
    "event_committed", "checkpoint_triggered", "checkpoint_created",
    "checkpoint_submitted", "checkpoint_confirmed", "checkpoint_failed",
    "verification_result",
}


def utc_now_iso() -> str:
    """UTC timestamp, ISO-8601, microsecond precision, 'Z' suffix."""
    return (datetime.now(timezone.utc).isoformat(timespec="microseconds")
            .replace("+00:00", "Z"))


def validate_run_id(run_id: str) -> str:
    if not isinstance(run_id, str) or not _RUN_ID_RE.match(run_id) or ".." in run_id:
        raise ValueError(f"invalid run_id {run_id!r}: use letters, digits, '_', '-', '.' (max 100)")
    return run_id


class Telemetry:
    """Append-only JSONL writer bound to one run directory."""

    def __init__(self, run_id: str, raw_root: Path | str | None = None,
                 fsync: bool = False, strict: bool = False):
        self.run_id = validate_run_id(run_id)
        self.raw_root = Path(raw_root) if raw_root else DEFAULT_RAW_ROOT
        self.run_dir = self.raw_root / self.run_id
        self.path = self.run_dir / "events.jsonl"
        self.fsync = fsync
        self.strict = strict
        self.dropped = 0
        self._lock = threading.Lock()
        self._fh = None

    def _open(self):
        if self._fh is None or self._fh.closed:
            self.run_dir.mkdir(parents=True, exist_ok=True)
            # line-buffered text, append mode: existing raw data is never truncated
            self._fh = open(self.path, "a", encoding="utf-8", buffering=1)
        return self._fh

    def emit(self, event_type: str, **fields: Any) -> dict | None:
        record = {
            "event_type": event_type,
            "ts_utc": utc_now_iso(),
            "mono_ns": time.monotonic_ns(),
            "run_id": self.run_id,
            **fields,
        }
        try:
            line = json.dumps(record, sort_keys=False, separators=(",", ":"),
                              allow_nan=False, default=_json_default)
        except (TypeError, ValueError) as exc:
            return self._fail(f"serialization error for {event_type}: {exc}", exc)
        try:
            with self._lock:
                fh = self._open()
                fh.write(line + "\n")   # single write => one complete line
                fh.flush()
                if self.fsync:
                    os.fsync(fh.fileno())
        except OSError as exc:
            return self._fail(f"write error for {event_type}: {exc}", exc)
        return record

    def _fail(self, msg: str, exc: Exception):
        self.dropped += 1
        print(f"[telemetry] {msg}", file=sys.stderr)
        if self.strict:
            raise exc
        return None

    def close(self):
        with self._lock:
            if self._fh and not self._fh.closed:
                self._fh.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def _json_default(obj):
    if isinstance(obj, (bytes, bytearray)):
        return obj.hex()
    if isinstance(obj, Path):
        return str(obj)
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    if hasattr(obj, "item"):  # numpy scalars
        return obj.item()
    raise TypeError(f"not JSON serializable: {type(obj).__name__}")


# ---- module-level convenience API (what Person 1 imports) -------------------
_active: Telemetry | None = None
_active_lock = threading.Lock()


def start_run(run_id: str, raw_root: Path | str | None = None, **kw) -> Telemetry:
    """Select the active run. Re-starting the same run_id appends (never truncates)."""
    global _active
    with _active_lock:
        if _active is not None:
            _active.close()
        _active = Telemetry(run_id, raw_root, **kw)
        return _active


def end_run() -> None:
    global _active
    with _active_lock:
        if _active is not None:
            _active.close()
        _active = None


def emit(event_type: str, **fields: Any) -> dict | None:
    """Emit to the active run. If no run is active (e.g. unit tests of Person 1's
    code) this is a no-op returning None, so importing it is always safe."""
    t = _active
    if t is None:
        return None
    return t.emit(event_type, **fields)
