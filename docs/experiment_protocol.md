# Experiment protocol (draft — freeze at Gate G4)

1. Write a config (`research/configs/*.json`); validate; record its SHA-256. Do not edit after freezing.
2. `RESEARCH_MODE=1`; target DB exactly `hms_research`; reset from `hms_template` via `research/reset.py`; anchor state
   cleared via `reset_anchor_research` (only `anchor_research`).
3. Start HMS (:8000) and anchor (:8001, `ANCHOR_TEST_MODE=1` for outage runs); check readiness.
4. Run workload + outage schedule; log planned **and** observed outage times.
5. Drain pending checkpoints; export telemetry; verify via Person 1's verifier (not implemented here).
6. Save manifest; status is `completed` only if every stage ran. Failed/incomplete runs are kept and reported.

Policies are compared on identical seeds (same schedule) → paired differences + seeded bootstrap CIs.
Outage scenarios to configure: none; just before a trigger; after creation before confirmation; spanning triggers;
repeated; backlog recovery; injected sequence gap (needs Person 1 hook — **not implemented**).

## Status of `run_experiment`
`--mode mock` runs the harness with a mock client (manifest status `mock_completed`). `--mode real` stops at the
first stage needing Person 1's HMS and records `incomplete`.
