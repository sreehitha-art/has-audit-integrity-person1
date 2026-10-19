"""Mock-Person-1 helpers: build signed checkpoints without any Person 1 code."""
import hashlib
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from anchor_service.services.checkpoint import canonical_bytes, checkpoint_digest, sign_bytes

KEY_ID = "app-key-1"


def new_keypair():
    k = Ed25519PrivateKey.generate()
    return k, {KEY_ID: k.public_key()}


def make_checkpoint(seq=1, prev_digest=None, chain_id="hms-main", terminal_seq=None,
                    key_id=KEY_ID, policy="B"):
    ts = terminal_seq if terminal_seq is not None else seq * 10
    return {
        "version": 1, "chain_id": chain_id, "checkpoint_sequence": seq,
        "terminal_event_sequence": ts,
        "terminal_event_hash": hashlib.sha256(f"event-{ts}".encode()).hexdigest(),
        "previous_checkpoint_digest": prev_digest,
        "created_at": "2026-01-01T00:00:%02d.000000Z" % (seq % 60),
        "policy_name": policy, "policy_config_version": "policy.v1", "key_id": key_id,
    }


def sign(cp, key):
    c = canonical_bytes(cp)
    return sign_bytes(key, c), checkpoint_digest(c)


def chain(key, n, chain_id="hms-main"):
    """n linked, signed checkpoints: list of (checkpoint, signature, digest)."""
    out, prev = [], None
    for i in range(1, n + 1):
        cp = make_checkpoint(i, prev, chain_id)
        sig, dig = sign(cp, key)
        out.append((cp, sig, dig)); prev = dig
    return out
