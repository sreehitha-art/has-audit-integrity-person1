# Anchor service API contract — PROVISIONAL (port 8001)

All endpoints except `/health/ready` need `Authorization: Bearer <ANCHOR_API_TOKEN>`.
Errors: `{"error": {"code": "...", "message": "..."}}`.

| status | meaning |
|---|---|
| 400 | malformed checkpoint / bad idempotency key / invalid or unknown-key signature (`bad_request`, `invalid_signature`) |
| 401 | missing/invalid bearer (or test token) |
| 404 | unknown anchor_id; also `/v1/_test/fault` when `ANCHOR_TEST_MODE` ≠ 1 |
| 409 | sequence gap/regression, wrong previous digest, conflicting content, key reuse with different content |
| 503 | configured `down` fault (returned before any processing) |

## POST /v1/checkpoints
Request (envelope fields are **not** part of the signed bytes):
```json
{"checkpoint": {"version":1,"chain_id":"hms-main","checkpoint_sequence":1,"terminal_event_sequence":10,
  "terminal_event_hash":"<64 hex>","previous_checkpoint_digest":null,"created_at":"2026-01-01T00:00:01.000000Z",
  "policy_name":"B","policy_config_version":"policy.v1","key_id":"app-key-1"},
 "signature":"<base64 Ed25519>", "idempotency_key":"hms-main-cp-1"}
```
Response `201` (new) or `200` (retry of an already-retained checkpoint → **original** receipt):
```json
{"created": true, "receipt": {"anchor_id":"<uuid>","chain_id":"hms-main","checkpoint_sequence":1,
 "checkpoint_digest":"<64 hex>","terminal_event_sequence":10,"received_at":"...Z",
 "durably_retained_at":"...Z","idempotency_key":"hms-main-cp-1"}}
```
Decision order: bearer → structure → Ed25519 → idempotency → continuity → conflict → INSERT+COMMIT → receipt.
Idempotency scope: **global** (one key = one checkpoint, any chain). Same content under a *different* key is
also answered with the original receipt (no duplicate row).

## GET /v1/chains/{chain_id}/checkpoints → `{"chain_id":..., "checkpoints":[<full record + receipt>, ...]}` in sequence order
## GET /v1/checkpoints/{anchor_id} → full record + `receipt`
## GET /health/ready → `{"status":"ready"|"not_ready", ...}` readiness only; does not imply any checkpoint is retained
## POST /v1/_test/fault
Exists only when `ANCHOR_TEST_MODE=1`; requires header `X-Test-Token: <ANCHOR_TEST_TOKEN>`.
Body `{"mode":"ok|down|delay|drop_response","delay_seconds":N}` (0–600).
`drop_response` commits first, then holds the HTTP response for `delay_seconds`.

## Durability limits
A receipt is returned only after the SQL transaction commits. How strong that is depends on PostgreSQL
(`fsync`, `synchronous_commit`, storage, replication) — keep `fsync=on`, `synchronous_commit=on` for experiments
and record the settings in the manifest. A single-node database is not protection against its own operator.
Continuity checks are serialised by a process-level lock; run **one** anchor worker (the DB unique
constraints remain the final guard if you ever scale out).

## durably_retained_at semantics (needs Person 1's confirmation)
The guide describes it as "time after successful commit". Implemented value: the database clock
(`clock_timestamp()`) evaluated at the INSERT statement **inside the committing transaction**. Commit completes at
or after that instant, so it is a **lower bound** on commit completion, not a post-commit reading. A true
post-commit stamp would need a second write after commit, which creates a window where a concurrent retry could
observe a different receipt value; that trade-off was deliberately not taken. Receipts are identical on every retry.

## Concurrent / multi-worker behaviour
Unique constraints (`uq_chain_seq`, `uq_idempotency_key`) are the final arbiter. If a request loses a
check-then-insert race, the service re-reads and returns the **original** receipt (200) when the content is identical,
or 409 when it conflicts. Verified against SQLite (a stand-in) with 12 racing workers; not yet against PostgreSQL.

## Input validation added after review
`created_at` must be a real UTC calendar timestamp; sequences must be within [1, 2^63-1] (else 400 instead of a
database error); fault-control inputs must be finite numbers in range / valid strings (else 400).
