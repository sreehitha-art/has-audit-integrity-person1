"""Load trusted Ed25519 PUBLIC keys from a directory, separate from the checkpoint DB.

File convention: <key_id>.pub.pem  (e.g. app-key-1.pub.pem). Private keys are never
read here and must never be placed in this directory.
"""
from __future__ import annotations
from pathlib import Path
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


def load_trusted_keys(directory) -> dict[str, Ed25519PublicKey]:
    keys: dict[str, Ed25519PublicKey] = {}
    d = Path(directory)
    if not d.is_dir():
        return keys
    for p in sorted(d.glob("*.pub.pem")):
        data = p.read_bytes()
        if b"PRIVATE KEY" in data:
            raise ValueError(f"{p} contains a private key; refusing to load")
        key = serialization.load_pem_public_key(data)
        if not isinstance(key, Ed25519PublicKey):
            raise ValueError(f"{p} is not an Ed25519 public key")
        keys[p.name[: -len(".pub.pem")]] = key
    return keys
