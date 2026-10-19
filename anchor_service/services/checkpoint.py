"""Checkpoint contract: canonical serialization, digest, Ed25519 verification.

PROVISIONAL CONTRACT (to be confirmed with Person 1 at Gate G1)
---------------------------------------------------------------
* The signed object is the JSON object with exactly the 10 fields in
  CHECKPOINT_FIELDS.
* canonical bytes = json.dumps(obj, sort_keys=True, separators=(",", ":"),
  ensure_ascii=True, allow_nan=False).encode("utf-8")   (same rule as the guide's
  canonical_bytes for audit events)
* signature = Ed25519(private_key).sign(DOMAIN + canonical bytes)
  with DOMAIN = b"HMS-CHECKPOINT-v1"  (domain separation from event hashes)
* checkpoint_digest = SHA-256(DOMAIN + canonical bytes), lowercase hex
* signature wire format = standard base64 (RFC 4648, padded) of the 64 raw bytes
* terminal_event_hash / previous_checkpoint_digest = lowercase hex (64 chars) or
  null for previous_checkpoint_digest of checkpoint_sequence == 1
* created_at = UTC ISO-8601 with 'Z' suffix
The DOMAIN prefix is NOT specified by the guide; it is a documented assumption.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
from dataclasses import dataclass

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

CHECKPOINT_DOMAIN = b"HMS-CHECKPOINT-v1"
CHECKPOINT_FIELDS = (
    "version", "chain_id", "checkpoint_sequence", "terminal_event_sequence",
    "terminal_event_hash", "previous_checkpoint_digest", "created_at",
    "policy_name", "policy_config_version", "key_id",
)
POLICY_NAMES = {"A", "B", "C-risk", "C-unit"}
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_TS = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?Z$")


class CheckpointError(ValueError):
    """Malformed checkpoint (maps to HTTP 400)."""


class SignatureError(CheckpointError):
    """Bad signature / unknown key (maps to HTTP 400)."""


def canonical_bytes(obj: dict) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode("utf-8")


def checkpoint_digest(canonical: bytes) -> str:
    return hashlib.sha256(CHECKPOINT_DOMAIN + canonical).hexdigest()


def sign_bytes(private_key, canonical: bytes) -> str:
    """Test/mock-Person-1 helper. The anchor service itself never signs."""
    return base64.b64encode(private_key.sign(CHECKPOINT_DOMAIN + canonical)).decode("ascii")


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def validate_checkpoint_structure(cp: dict) -> None:
    if not isinstance(cp, dict):
        raise CheckpointError("checkpoint must be a JSON object")
    missing = [f for f in CHECKPOINT_FIELDS if f not in cp]
    extra = [f for f in cp if f not in CHECKPOINT_FIELDS]
    if missing:
        raise CheckpointError(f"missing fields: {missing}")
    if extra:
        raise CheckpointError(f"unexpected fields: {extra}")
    if not _is_int(cp["version"]) or cp["version"] != 1:
        raise CheckpointError("version must be integer 1")
    for f in ("chain_id", "policy_config_version", "key_id"):
        if not isinstance(cp[f], str) or not cp[f] or len(cp[f]) > 200:
            raise CheckpointError(f"{f} must be a non-empty string (<=200 chars)")
    if not _is_int(cp["checkpoint_sequence"]) or cp["checkpoint_sequence"] < 1:
        raise CheckpointError("checkpoint_sequence must be an integer >= 1")
    if not _is_int(cp["terminal_event_sequence"]) or cp["terminal_event_sequence"] < 1:
        raise CheckpointError("terminal_event_sequence must be an integer >= 1")
    if not isinstance(cp["terminal_event_hash"], str) or not _HEX64.match(cp["terminal_event_hash"]):
        raise CheckpointError("terminal_event_hash must be 64 lowercase hex chars")
    prev = cp["previous_checkpoint_digest"]
    if cp["checkpoint_sequence"] == 1:
        if prev is not None:
            raise CheckpointError("previous_checkpoint_digest must be null for checkpoint_sequence 1")
    elif not isinstance(prev, str) or not _HEX64.match(prev):
        raise CheckpointError("previous_checkpoint_digest must be 64 lowercase hex chars")
    if not isinstance(cp["created_at"], str) or not _TS.match(cp["created_at"]):
        raise CheckpointError("created_at must be UTC ISO-8601 ending in 'Z'")
    if cp["policy_name"] not in POLICY_NAMES:
        raise CheckpointError(f"policy_name must be one of {sorted(POLICY_NAMES)}")


def decode_signature(sig_b64: str) -> bytes:
    if not isinstance(sig_b64, str):
        raise SignatureError("signature must be a base64 string")
    try:
        sig = base64.b64decode(sig_b64, validate=True)
    except (binascii.Error, ValueError):
        raise SignatureError("signature is not valid base64")
    if len(sig) != 64:
        raise SignatureError("Ed25519 signature must be 64 bytes")
    return sig


@dataclass(frozen=True)
class VerifiedCheckpoint:
    checkpoint: dict
    canonical: bytes
    digest: str
    signature_b64: str


def verify_signed_checkpoint(cp: dict, signature_b64: str, trusted_keys: dict) -> VerifiedCheckpoint:
    """Validate structure, select key by key_id, verify Ed25519 over DOMAIN+canonical."""
    validate_checkpoint_structure(cp)
    sig = decode_signature(signature_b64)
    pub = trusted_keys.get(cp["key_id"])
    if pub is None:
        raise SignatureError(f"unknown key_id {cp['key_id']!r}")
    canonical = canonical_bytes(cp)
    try:
        pub.verify(sig, CHECKPOINT_DOMAIN + canonical)
    except InvalidSignature:
        raise SignatureError("signature verification failed")
    return VerifiedCheckpoint(cp, canonical, checkpoint_digest(canonical), signature_b64)
