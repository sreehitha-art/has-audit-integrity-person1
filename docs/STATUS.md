# Implementation status (honest ledger)

Test run in the build sandbox (Python 3.12, **no network, no PostgreSQL, no FastAPI/SQLAlchemy/pytest/httpx**):
- `anchor_service/tests`: 32 collected → **23 passed, 9 skipped** (skips = HTTP + PostgreSQL tests that need packages/DB)
- `research/tests`: **51 passed**
- Total: **74 passed, 0 failed, 9 skipped**. Run with `unittest`; tests are also pytest-compatible but pytest was not run.

| Component | State |
|---|---|
| Telemetry (Phase 1) | Implemented, tested (concurrency, timestamps, location, failure survival) |
| Checkpoint contract, Ed25519 verify, trusted keys, key generator | Implemented, tested (valid/modified/wrong-key/unknown-key/malformed). `generate_keys.py` not executed |
| Anchor core logic (idempotency, continuity, conflicts, fault modes incl. drop_response→retry) | Implemented, tested with an **in-memory** repository |
| FastAPI app (`main.py`), SQLAlchemy repo, Alembic migration, `schema.sql` | Written, **syntax-checked only — never executed**. HTTP behaviour (401/400/409/503, test-endpoint hiding) and PostgreSQL behaviour (commit-before-receipt, unique constraints, restart persistence) are covered by tests that are written but **skipped here; must be run locally** |
| Experiment config, 3 workloads, I_D, tolerance checks | Implemented, tested |
| Async workload client | Tested with mock client only. `HttpxHmsClient` endpoints are **provisional guesses**, never run |
| Tamper runner + safety guards + mock verifier | Guards tested. Results are mock-labelled. `HmsVerifierAdapter` is a placeholder |
| Reset procedure | Guards/SQL generation tested with an injected executor; **never run against PostgreSQL**. Uses `anchor_research` as the anchor research DB name (my addition — the guide doesn't name one) |
| Experiment runner / outage controller / manifest | Mock mode tested. Real mode intentionally records `incomplete`. PostgreSQL version not collected yet. Injected-sequence-gap scenario not implemented (needs Person 1 hook) |
| Metrics + models + Monte Carlo (C-risk) | Implemented; closed forms (B, A at high rate, renewal) checked against simulation. **Not validated on real system data** |
| Analysis pipeline + dashboard/charts | Implemented, tested on synthetic telemetry with known answers; reproducible output |
| Dashboard monitoring of live anchor (requested/pending states from the HMS) | Only derives from telemetry; no live view |

## Unresolved assumptions (need Person 1)
1. `CHECKPOINT_DOMAIN` prefix `HMS-CHECKPOINT-v1` for signature and digest.
2. Hex encoding for hashes; base64 signature; envelope shape `{checkpoint, signature, idempotency_key}`.
3. Idempotency key scope = global.
4. Telemetry field names (`docs/telemetry_interface.md`); clock alignment across processes.
5. How the HMS derives `risk_class` from requests; HMS endpoint paths/auth for the workload adapter.
6. Verifier API shape for `HmsVerifierAdapter`.
7. Policy A at very low rate: "needs ≥1 eligible event" makes T/2+E[D] inapplicable (tested/documented).
