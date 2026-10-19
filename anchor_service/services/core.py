"""Storage-independent anchor logic.

``AnchorCore`` implements the POST /v1/checkpoints decision order from the guide:
  1 bearer token (done by the API layer)  2 parse/validate  3 verify Ed25519
  4 idempotency  5 sequence + previous-digest continuity  6 reject conflicts (409)
  7 insert + COMMIT  8 only then return the receipt.

It talks to a ``Repository`` (SQLAlchemy/PostgreSQL in production, in-memory in unit
tests). The repository's ``insert`` MUST be durable (committed) when it returns.
"""
from __future__ import annotations

import math
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol

from .checkpoint import CheckpointError, SignatureError, VerifiedCheckpoint, verify_signed_checkpoint


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


class AnchorError(Exception):
    status = 500
    code = "internal_error"

    def __init__(self, message: str):
        super().__init__(message); self.message = message


class BadRequest(AnchorError):
    status, code = 400, "bad_request"

class InvalidSignature(AnchorError):
    status, code = 400, "invalid_signature"

class Conflict(AnchorError):
    status, code = 409, "conflict"

class Unavailable(AnchorError):
    status, code = 503, "service_unavailable"


@dataclass
class Record:
    anchor_id: str
    chain_id: str
    checkpoint_sequence: int
    terminal_event_sequence: int
    terminal_event_hash: str
    previous_checkpoint_digest: str | None
    checkpoint_digest: str
    canonical_checkpoint: str
    signature: str
    key_id: str
    received_at: str
    durably_retained_at: str
    idempotency_key: str

    def receipt(self) -> dict:
        return {
            "anchor_id": self.anchor_id, "chain_id": self.chain_id,
            "checkpoint_sequence": self.checkpoint_sequence,
            "checkpoint_digest": self.checkpoint_digest,
            "terminal_event_sequence": self.terminal_event_sequence,
            "received_at": self.received_at,
            "durably_retained_at": self.durably_retained_at,
            "idempotency_key": self.idempotency_key,
        }

    def as_dict(self) -> dict:
        d = dict(self.__dict__); d["receipt"] = self.receipt(); return d


class Repository(Protocol):
    def get_by_idempotency_key(self, key: str) -> Record | None: ...
    def get_by_chain_seq(self, chain_id: str, seq: int) -> Record | None: ...
    def get_by_id(self, anchor_id: str) -> Record | None: ...
    def list_chain(self, chain_id: str) -> list[Record]: ...
    def last_in_chain(self, chain_id: str) -> Record | None: ...
    def insert(self, rec: Record) -> Record:
        """Durably insert (commit). Raise Conflict on unique-constraint violation."""
    def ping(self) -> bool: ...
    def count(self) -> int: ...


class InMemoryRepository:
    """Test double. Same uniqueness rules as the DB schema."""
    def __init__(self):
        self._lock = threading.Lock()
        self.by_id: dict[str, Record] = {}

    def get_by_idempotency_key(self, key):
        return next((r for r in self.by_id.values() if r.idempotency_key == key), None)
    def get_by_chain_seq(self, chain_id, seq):
        return next((r for r in self.by_id.values()
                     if r.chain_id == chain_id and r.checkpoint_sequence == seq), None)
    def get_by_id(self, anchor_id): return self.by_id.get(anchor_id)
    def list_chain(self, chain_id):
        return sorted((r for r in self.by_id.values() if r.chain_id == chain_id),
                      key=lambda r: r.checkpoint_sequence)
    def last_in_chain(self, chain_id):
        l = self.list_chain(chain_id); return l[-1] if l else None
    def insert(self, rec):
        with self._lock:
            if self.get_by_idempotency_key(rec.idempotency_key):
                raise Conflict("idempotency_key already used")
            if self.get_by_chain_seq(rec.chain_id, rec.checkpoint_sequence):
                raise Conflict("(chain_id, checkpoint_sequence) already retained")
            rec.durably_retained_at = now_iso()      # mimics DB clock at commit
            self.by_id[rec.anchor_id] = rec
            return rec
    def ping(self): return True
    def count(self): return len(self.by_id)


@dataclass
class FaultState:
    mode: str = "ok"            # ok | down | delay | drop_response
    delay_seconds: float = 0.0
    started_at_utc: str | None = None
    started_mono_ns: int | None = None


FAULT_MODES = {"ok", "down", "delay", "drop_response"}


class AnchorCore:
    def __init__(self, repo: Repository, trusted_keys: dict, on_fault_event=None):
        self.repo = repo
        self.trusted_keys = trusted_keys
        self.fault = FaultState()
        self._fault_lock = threading.Lock()
        self._ingest_lock = threading.Lock()   # serialises continuity check + insert (single process)
        self._on_fault_event = on_fault_event or (lambda *a, **k: None)

    # -- fault control (test mode only; authorisation enforced by API layer) --
    def set_fault(self, mode, delay_seconds=0.0) -> dict:
        """Inputs are untrusted JSON values: validate type AND finiteness (NaN/inf/str/bool -> 400)."""
        if not isinstance(mode, str) or mode not in FAULT_MODES:
            raise BadRequest(f"mode must be one of {sorted(FAULT_MODES)}")
        if (isinstance(delay_seconds, bool) or not isinstance(delay_seconds, (int, float))
                or not math.isfinite(delay_seconds)):
            raise BadRequest("delay_seconds must be a finite number")
        if delay_seconds < 0 or delay_seconds > 600:
            raise BadRequest("delay_seconds must be within [0, 600]")
        delay_seconds = float(delay_seconds)
        with self._fault_lock:
            prev = self.fault
            if prev.mode != "ok":      # observed end of previous fault
                self._on_fault_event("anchor_fault_end", mode=prev.mode,
                                     started_at_utc=prev.started_at_utc,
                                     started_mono_ns=prev.started_mono_ns)
            self.fault = FaultState(mode, delay_seconds,
                                    now_iso() if mode != "ok" else None,
                                    time.monotonic_ns() if mode != "ok" else None)
            if mode != "ok":
                self._on_fault_event("anchor_fault_start", mode=mode, delay_seconds=delay_seconds)
            return {"mode": mode, "delay_seconds": delay_seconds}

    def ingest(self, body: dict) -> tuple[Record, bool]:
        """Returns (record, created). May sleep for delay/drop_response faults:
        the *transport* layer decides what to do with ``post_commit_delay``."""
        rec, created, _ = self.ingest_with_fault(body)
        return rec, created

    def ingest_with_fault(self, body: dict):
        fault = self.fault
        if fault.mode == "down":
            raise Unavailable("anchor service is in configured down state")
        if fault.mode == "delay" and fault.delay_seconds:
            time.sleep(fault.delay_seconds)
        rec, created = self._process(body)
        post_commit_delay = fault.delay_seconds if fault.mode == "drop_response" else 0.0
        return rec, created, post_commit_delay

    def _process(self, body: dict) -> tuple[Record, bool]:
        received_at = now_iso()
        if not isinstance(body, dict):
            raise BadRequest("body must be a JSON object")
        key = body.get("idempotency_key")
        if not isinstance(key, str) or not (1 <= len(key) <= 200):
            raise BadRequest("idempotency_key must be a string of 1..200 chars")
        cp, sig = body.get("checkpoint"), body.get("signature")
        try:
            v: VerifiedCheckpoint = verify_signed_checkpoint(cp, sig, self.trusted_keys)
        except CheckpointError as exc:
            cls = InvalidSignature if isinstance(exc, SignatureError) else BadRequest
            raise cls(str(exc))
        canonical_text = v.canonical.decode("ascii")

        with self._ingest_lock:
            # (4) idempotency
            existing = self.repo.get_by_idempotency_key(key)
            if existing is not None:
                if (existing.checkpoint_digest == v.digest
                        and existing.chain_id == cp["chain_id"]):
                    return existing, False          # original receipt
                raise Conflict("idempotency_key already used with different checkpoint content")
            # same logical identity without matching key
            same_seq = self.repo.get_by_chain_seq(cp["chain_id"], cp["checkpoint_sequence"])
            if same_seq is not None:
                if same_seq.checkpoint_digest == v.digest:
                    # identical content under a different key: return original, do not duplicate
                    return same_seq, False
                raise Conflict("checkpoint_sequence already retained with different content")
            # (5) continuity
            last = self.repo.last_in_chain(cp["chain_id"])
            expected_seq = 1 if last is None else last.checkpoint_sequence + 1
            if cp["checkpoint_sequence"] != expected_seq:
                raise Conflict(f"sequence gap/regression: expected {expected_seq}, "
                               f"got {cp['checkpoint_sequence']}")
            expected_prev = None if last is None else last.checkpoint_digest
            if cp["previous_checkpoint_digest"] != expected_prev:
                raise Conflict("previous_checkpoint_digest does not match retained chain")
            if last is not None and cp["terminal_event_sequence"] < last.terminal_event_sequence:
                raise Conflict("terminal_event_sequence regressed")
            rec = Record(
                anchor_id=str(uuid.uuid4()), chain_id=cp["chain_id"],
                checkpoint_sequence=cp["checkpoint_sequence"],
                terminal_event_sequence=cp["terminal_event_sequence"],
                terminal_event_hash=cp["terminal_event_hash"],
                previous_checkpoint_digest=cp["previous_checkpoint_digest"],
                checkpoint_digest=v.digest, canonical_checkpoint=canonical_text,
                signature=v.signature_b64, key_id=cp["key_id"],
                received_at=received_at, durably_retained_at="",  # set by repo after commit
                idempotency_key=key)
            try:
                return self.repo.insert(rec), True      # insert() returns only after COMMIT
            except Conflict:
                # Lost a check-then-insert race against another worker/process (the DB unique
                # constraints are the real arbiter). Re-resolve: an identical logical request must
                # get the ORIGINAL receipt, not a 409; genuinely conflicting content stays 409.
                won = self.repo.get_by_idempotency_key(key)
                if won is not None:
                    if won.checkpoint_digest == v.digest and won.chain_id == cp["chain_id"]:
                        return won, False
                    raise Conflict("idempotency_key already used with different checkpoint content")
                won = self.repo.get_by_chain_seq(cp["chain_id"], cp["checkpoint_sequence"])
                if won is not None and won.checkpoint_digest == v.digest:
                    return won, False
                raise
