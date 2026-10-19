# Checkpoint format — PROVISIONAL (confirm with Person 1 at Gate G1)

The field list comes from the Implementation Guide. The items marked **ASSUMPTION** are *not*
in the guide; they were chosen so the service is testable and must be confirmed.

## Signed object (exactly these 10 fields, no extras)
| field | type | rule |
|---|---|---|
| version | int | must be `1` |
| chain_id | string | 1–200 chars |
| checkpoint_sequence | int ≥ 1 | consecutive per chain |
| terminal_event_sequence | int ≥ 1 | non-decreasing |
| terminal_event_hash | string | 64 lowercase hex — **ASSUMPTION** hex encoding |
| previous_checkpoint_digest | string \| null | null iff sequence = 1; else 64 lowercase hex |
| created_at | string | UTC ISO-8601 ending `Z` |
| policy_name | string | `A`, `B`, `C-risk`, `C-unit` |
| policy_config_version | string | 1–200 chars |
| key_id | string | selects the trusted public key |

## Canonical bytes
`json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("utf-8")`
(the same rule as the guide's `canonical_bytes` for audit events).

## Signature input and digest  (**ASSUMPTION**: domain prefix)
- `signature = Ed25519.sign(b"HMS-CHECKPOINT-v1" + canonical_bytes)`
- wire form: standard base64 (padded) of the 64 raw bytes
- `checkpoint_digest = hex(SHA-256(b"HMS-CHECKPOINT-v1" + canonical_bytes))`
- `canonical_checkpoint` is stored as the ASCII text of the canonical bytes.

If Person 1 signs without the domain prefix, change `CHECKPOINT_DOMAIN` in
`anchor_service/services/checkpoint.py` (one constant) and update tests — do not do this silently.

## Example
Generate a valid example with the helpers in
`anchor_service/tests/helpers.py` (`make_checkpoint`, `sign`).
