import base64, copy, tempfile, unittest
from pathlib import Path
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from anchor_service.services.checkpoint import (
    CheckpointError, SignatureError, verify_signed_checkpoint, canonical_bytes, checkpoint_digest)
from anchor_service.services.trusted_keys import load_trusted_keys
from anchor_service.tests.helpers import make_checkpoint, sign, new_keypair, KEY_ID


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.key, self.trusted = new_keypair()

    def test_valid_signature_accepted(self):
        cp = make_checkpoint(1); sig, dig = sign(cp, self.key)
        v = verify_signed_checkpoint(cp, sig, self.trusted)
        self.assertEqual(v.digest, dig)

    def test_canonical_bytes_known_vector(self):
        cp = make_checkpoint(1)
        self.assertEqual(canonical_bytes(cp), canonical_bytes(dict(reversed(list(cp.items())))))
        self.assertNotIn(b" ", canonical_bytes(cp))
        self.assertEqual(len(checkpoint_digest(canonical_bytes(cp))), 64)

    def test_modified_contents_rejected(self):
        cp = make_checkpoint(1); sig, _ = sign(cp, self.key)
        bad = copy.deepcopy(cp); bad["terminal_event_sequence"] += 1
        with self.assertRaises(SignatureError):
            verify_signed_checkpoint(bad, sig, self.trusted)

    def test_wrong_key_signature_rejected(self):
        cp = make_checkpoint(1); sig, _ = sign(cp, Ed25519PrivateKey.generate())
        with self.assertRaises(SignatureError):
            verify_signed_checkpoint(cp, sig, self.trusted)

    def test_unknown_key_id_rejected(self):
        cp = make_checkpoint(1, key_id="other-key"); sig, _ = sign(cp, self.key)
        with self.assertRaises(SignatureError) as cm:
            verify_signed_checkpoint(cp, sig, self.trusted)
        self.assertIn("unknown key_id", str(cm.exception))

    def test_malformed_signature_rejected(self):
        cp = make_checkpoint(1)
        for s in ["!!!notbase64", base64.b64encode(b"short").decode(), None, 123]:
            with self.assertRaises(SignatureError):
                verify_signed_checkpoint(cp, s, self.trusted)

    def test_structure_validation(self):
        sig, _ = sign(make_checkpoint(1), self.key)
        def mut(**kw):
            cp = make_checkpoint(2, "a" * 64); cp.update(kw); return cp
        cases = [
            mut(version=2), mut(checkpoint_sequence=0), mut(checkpoint_sequence=True),
            mut(terminal_event_hash="XYZ"), mut(previous_checkpoint_digest=None),
            mut(created_at="2026-01-01 00:00:00"), mut(policy_name="Z"), mut(chain_id=""),
        ]
        for cp in cases:
            with self.assertRaises(CheckpointError):
                verify_signed_checkpoint(cp, sig, self.trusted)
        cp = make_checkpoint(1); del cp["policy_name"]
        with self.assertRaises(CheckpointError):
            verify_signed_checkpoint(cp, sig, self.trusted)
        cp = make_checkpoint(1); cp["extra"] = 1
        with self.assertRaises(CheckpointError):
            verify_signed_checkpoint(cp, sig, self.trusted)
        first = make_checkpoint(1, "a" * 64)  # seq 1 must have null previous
        with self.assertRaises(CheckpointError):
            verify_signed_checkpoint(first, sig, self.trusted)

    def test_trusted_key_loading_and_private_key_refused(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            (p / f"{KEY_ID}.pub.pem").write_bytes(self.key.public_key().public_bytes(
                serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
            keys = load_trusted_keys(p)
            self.assertEqual(list(keys), [KEY_ID])
            cp = make_checkpoint(1); sig, _ = sign(cp, self.key)
            verify_signed_checkpoint(cp, sig, keys)
            (p / "evil.pub.pem").write_bytes(self.key.private_bytes(
                serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption()))
            with self.assertRaises(ValueError):
                load_trusted_keys(p)

if __name__ == "__main__":
    unittest.main()
