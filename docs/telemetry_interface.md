# Telemetry interface for Person 1 (not yet integrated — Person 1's code was not modified)

```python
from research import telemetry            # or copy research/telemetry.py
telemetry.start_run("run-2026-01-smoke")  # once per process; same run_id appends, never truncates
telemetry.emit("event_committed", event_seq=42)   # call AFTER db.commit(), never inside a transaction
```
Each line of `research/raw/<run_id>/events.jsonl` gets `event_type, ts_utc, mono_ns, run_id` automatically.
`emit` is a no-op when no run is active and never raises (failures counted in `Telemetry.dropped`).

**Provisional field names the analysis expects** (confirm at Gate G2; analysis reads only these):
| event_type | fields |
|---|---|
| event_committed | `event_seq` |
| checkpoint_triggered | `reason`, `policy_name` |
| checkpoint_created | `checkpoint_seq`, `terminal_event_seq` |
| checkpoint_submitted | `checkpoint_seq`, `attempt` |
| checkpoint_confirmed | `checkpoint_seq`, `anchor_id` |
| checkpoint_failed | `checkpoint_seq`, `error` |
| verification_result | `outcome` (verified/mismatch/inconclusive), `scope` |

Time alignment: analysis uses `mono_ns`, so emit from the **same process/host** as the experiment runner or
agree a clock-offset method. Multi-process runs need this resolved before final experiments.
