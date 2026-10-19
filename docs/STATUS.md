# Implementation status — verified results only

Environment of the last verification run: Python 3.12, numpy 2.4.4, cryptography 46.0.6, stdlib `sqlite3`.
**Not available:** network, FastAPI, httpx, SQLAlchemy, Alembic, psycopg2, pytest, PostgreSQL, Docker.
Command: `python scripts/run_all_tests.py` →

| suite | run | passed | failed | skipped |
|---|---|---|---|---|
| anchor_service/tests | 58 | 44 | 0 | 14 |
| research/tests | 70 | 70 | 0 | 0 |
| **total** | **128** | **114** | **0** | **14** |

## 1. Executed and passing (114)
- **Telemetry:** concurrent JSONL validity, timestamps, run location, append-on-restart, failure survival, serialization-error handling, reserved-field protection.
- **Checkpoint contract:** Ed25519 valid / modified / wrong-key / unknown-key / malformed signature; structure rules; calendar-valid timestamps; int64 bounds; trusted-key loading (private key refused).
- **Anchor core (in-memory and SQLite stand-in):** idempotent retry, key reuse conflict, same-sequence conflict, gaps, wrong previous digest, independent chains, down / delay / drop_response faults, lost-response-then-retry returns original receipt, fault-input validation.
- **Concurrency:** 48 threads on one request → 1 record, identical receipt, exactly one "created"; 24 conflicting contents for one sequence → exactly one winner; out-of-order concurrent submission → contiguous chain; **12 independent workers with no shared lock racing on SQLite** → 1 row, identical receipts, no 409/500.
- **Durability (SQLite stand-in):** receipt only after COMMIT (row visible to an independent connection); injected commit failure → no receipt, no row, same request succeeds afterwards; persistence across a real separate process restart, with retry returning the original receipt; DB-level constraints enforced individually.
- **Schema:** static three-way consistency (ORM model ↔ Alembic migration ↔ schema.sql ↔ SQLite DDL): columns, nullability, unique constraints. Mutation-checked.
- **Workloads:** same seed ⇒ same schedule across processes and `PYTHONHASHSEED` values; schedule independent of policy; golden digest pinned to numpy 2.4.4; rate tolerance; dispersion ordering steady < Poisson ≈ 1 < bursty.
- **Metrics/models:** confirmation-based vulnerability, uncovered events, out-of-order confirmation, failed-only checkpoints, recovery lag (incl. already-covered backlog), error metrics, bootstrap; Policy B/A/renewal formulas vs simulation; C-risk Monte Carlo limiting cases.
- **Runner/outage/tamper/reset/analysis:** mock run manifest and logs; outage failures raised and logged, anchor restoration attempted, run marked `failed`; real mode `incomplete`; tamper/reset safety guards; analysis reproducible byte-for-byte.
- Mutation checks: removing the race fix makes 3 tests fail (incl. the multi-worker SQLite race); introducing schema drift makes the consistency test fail.

## 2. Still blocked (14 skipped) and why
| tests | count | blocker |
|---|---|---|
| `test_api.py` (auth, status codes, GET endpoints, readiness, fault endpoint hidden/token/400s, bad fault values → 400, bad date/sequence → 400, drop_response over HTTP) | 9 | FastAPI + httpx not installable offline |
| `test_api_postgres.py` (restart persistence, DB constraints, independent-session visibility, multi-worker race, migration-vs-ORM inspection) | 5 | no PostgreSQL, SQLAlchemy, psycopg2, Alembic |

Also never executed: `main.py`, `sql_repository.py`, Alembic migration, `generate_keys.py`, `HttpxHmsClient`, `http_set_fault`, `docker-compose.yml`, `setup_postgres.sql`, `scripts/preflight.py` beyond the "everything missing" path. SQLite results do **not** establish PostgreSQL behaviour (clock_timestamp, isolation, fsync, psycopg2 error mapping).

## 3. Bugs fixed this round
| # | bug (confirmed by probe before fixing) | files |
|---|---|---|
| 1 | Impossible timestamps (`2026-13-45T99:99:99Z`) accepted | `services/checkpoint.py` |
| 2 | Sequence > int64 passed validation → would 500 at INSERT | `services/checkpoint.py` |
| 3 | `set_fault` accepted NaN delay (later `sleep` crash) and crashed with TypeError on strings → 500 | `services/core.py`, `main.py` |
| 4 | Worker losing a check-then-insert race got 409 instead of the original receipt (breaks lost-response retry) | `services/core.py` |
| 5 | Telemetry callers could override `ts_utc/mono_ns/run_id`; `event_type=` kwarg raised TypeError | `research/telemetry.py` |
| 6 | Config with NaN/inf/wrong types raised ValueError/TypeError, not ConfigError | `research/experiment_config.py` |
| 7 | Outage-thread errors swallowed (run reported complete); fault could stay active after a crash | `research/outage.py`, `research/scripts/run_experiment.py` |
| 8 | `recovery_lag` positive when backlog was already covered before outage end | `research/analysis/metrics.py` |
| 9 | Manifest lacked package versions (RNG streams are numpy-version dependent) | `research/manifest.py` |
| 10 | Postgres constraint test reused seq 1, so it did not isolate the idempotency constraint | `tests/test_api_postgres.py` |
| 11 | Two weak/flawed tests of my own (race double stayed stale forever; vacuous 409 assertions) | `tests/test_hardening.py` |
| — | `durably_retained_at` comment claimed commit-time; it is INSERT-time (lower bound). Documented, not changed | `models/db_models.py`, `docs/anchor_api_contract.md` |

New files: `services/sqlite_repository.py`, `tests/test_hardening.py` (both suites), `tests/test_schema_consistency.py`, `research/tests/golden_schedule_digests.json`, `scripts/preflight.py`, `scripts/run_all_tests.py`, `docker-compose.yml`, `scripts/setup_postgres.sql`.

## 4. Needs confirmation from Person 1 (nothing here is a verified contract)
1. Signature/digest domain prefix `HMS-CHECKPOINT-v1`; hex hashes; base64 signature; envelope `{checkpoint, signature, idempotency_key}`.
2. Meaning of `durably_retained_at` (INSERT-time lower bound vs post-commit).
3. Idempotency-key scope (global) and "same content, different key ⇒ original receipt".
4. Telemetry event/field names and clock alignment across processes.
5. HMS endpoint paths/auth, how `risk_class` is derived, verifier API shape, how a sequence-gap can be injected.

## 5. Exact next steps before integration
1. `pip install -r requirements.txt`; `python scripts/preflight.py`; run `python -m unittest anchor_service.tests.test_api` → the 7 HTTP tests. Fix anything they reveal (main.py has never run).
2. Start a disposable PostgreSQL (`docker compose up -d` or your own), set `ANCHOR_DATABASE_URL` and `ANCHOR_TEST_DATABASE_URL` to the *same disposable* DB, `alembic upgrade head`, then run `python scripts/run_all_tests.py --strict` (goal: 0 skipped).
3. Run `alembic downgrade base && alembic upgrade head` once to check the migration both ways.
4. Start the service (`python -m anchor_service.main`) and exercise it with `curl` using a real signed checkpoint.
5. Send Person 1 `docs/checkpoint_format.md`, `docs/anchor_api_contract.md`, `docs/telemetry_interface.md`; resolve §4; only then implement `HmsVerifierAdapter` and the real runner stages.
6. Pin dependency versions after step 1 (`pip freeze`) and record them via the manifest.
