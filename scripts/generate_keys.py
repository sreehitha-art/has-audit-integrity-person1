"""Generate an Ed25519 TEST key pair.

  python scripts/generate_keys.py --key-id app-key-1 --out-private ./local_keys \
      --out-public anchor_service/trusted_keys

The private key goes OUTSIDE the repo (or into git-ignored *.pem) and belongs to the
signer (Person 1's environment in production; test-only for Person 2). Only the
.pub.pem file is meant to be shared/committed.
"""
import argparse, os
from pathlib import Path
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--key-id", default="app-key-1")
    ap.add_argument("--out-private", required=True)
    ap.add_argument("--out-public", default="anchor_service/trusted_keys")
    a = ap.parse_args()
    priv = Ed25519PrivateKey.generate()
    priv_dir, pub_dir = Path(a.out_private), Path(a.out_public)
    priv_dir.mkdir(parents=True, exist_ok=True); pub_dir.mkdir(parents=True, exist_ok=True)
    pf = priv_dir / f"{a.key_id}.pem"
    if pf.exists():
        raise SystemExit(f"{pf} already exists; refusing to overwrite")
    fd = os.open(pf, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(priv.private_bytes(serialization.Encoding.PEM,
                                   serialization.PrivateFormat.PKCS8,
                                   serialization.NoEncryption()))
    (pub_dir / f"{a.key_id}.pub.pem").write_bytes(priv.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
    print(f"private: {pf} (KEEP SECRET, do not commit)\npublic:  {pub_dir / (a.key_id + '.pub.pem')}")

if __name__ == "__main__":
    main()
