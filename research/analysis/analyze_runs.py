"""Raw telemetry -> metrics JSON (+ charts + dashboard). Everything is derived from
archived files under research/raw/<run_id>/; no numbers are hard-coded.

PROVISIONAL field names expected from Person 1's telemetry (documented in
docs/telemetry_interface.md, to be confirmed at Gate G2):
  event_committed      : event_seq
  checkpoint_created   : checkpoint_seq, terminal_event_seq
  checkpoint_submitted : checkpoint_seq
  checkpoint_confirmed : checkpoint_seq, receipt (optional)
  checkpoint_failed    : checkpoint_seq
Runs without these events (e.g. mock harness runs) get exposure metrics = null, never
fabricated values.
"""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
import numpy as np

from . import metrics as m
from .. import telemetry as _t


def read_events(path: Path) -> list[dict]:
    out = []
    for i, line in enumerate(path.read_text().splitlines(), 1):
        if not line.strip(): continue
        try: out.append(json.loads(line))
        except json.JSONDecodeError: out.append({"event_type": "_corrupt_line", "line": i})
    return out


def analyze_run(run_dir: Path) -> dict:
    run_dir = Path(run_dir)
    ev = read_events(run_dir / "events.jsonl")
    manifest = json.loads((run_dir / "manifest.json").read_text()) if (run_dir / "manifest.json").exists() else {}
    start = next((e for e in ev if e["event_type"] == "experiment_start"), None)
    t0 = start["mono_ns"] if start else (ev[0]["mono_ns"] if ev else 0)
    sec = lambda e: (e["mono_ns"] - t0) / 1e9
    res = {"run_id": run_dir.name, "status": manifest.get("status"), "mode": manifest.get("mode"),
           "seed": manifest.get("seed"), "config_sha256": manifest.get("config_sha256"),
           "mock": manifest.get("mode") == "mock", "n_events_total": len(ev),
           "n_corrupt_lines": sum(e["event_type"] == "_corrupt_line" for e in ev)}

    reqs = [e for e in ev if e["event_type"] == "request_done" and not e.get("in_warmup")]
    res["requests"] = m.latency_summary([r["latency_ms"] for r in reqs], [r["ok"] for r in reqs]) if reqs else None
    res["n_timeouts"] = sum(r.get("error") == "timeout" for r in reqs)

    committed = [{"seq": e["event_seq"], "commit_t": sec(e)} for e in ev if e["event_type"] == "event_committed"]
    cps: dict[int, dict] = {}
    for e in ev:
        t = e["event_type"]
        if t == "checkpoint_created":
            cps[e["checkpoint_seq"]] = {"seq": e["checkpoint_seq"], "terminal_event_sequence": e["terminal_event_seq"],
                                        "created_t": sec(e), "confirmed_t": None, "state": "pending"}
        elif t == "checkpoint_confirmed" and e["checkpoint_seq"] in cps:
            cps[e["checkpoint_seq"]].update(confirmed_t=sec(e), state="confirmed")
        elif t == "checkpoint_failed" and e["checkpoint_seq"] in cps and cps[e["checkpoint_seq"]]["state"] != "confirmed":
            cps[e["checkpoint_seq"]]["state"] = "failed"
    cp_list = sorted(cps.values(), key=lambda c: c["seq"])
    res["checkpoint_counts"] = m.checkpoint_counts(cp_list) if cp_list else None

    if committed and cp_list:
        v = m.per_event_vulnerability(committed, cp_list)
        fin = v[np.isfinite(v)]
        res["vulnerability_s"] = {"n_events": len(v), "n_uncovered": int(np.isnan(v).sum()),
                                  "mean": float(fin.mean()) if len(fin) else None,
                                  "p50": float(np.percentile(fin, 50)) if len(fin) else None,
                                  "p95": float(np.percentile(fin, 95)) if len(fin) else None,
                                  "p99": float(np.percentile(fin, 99)) if len(fin) else None,
                                  "values": [float(x) for x in fin]}
        cfg = json.loads((run_dir / "config.json").read_text()) if (run_dir / "config.json").exists() else {}
        res["delayed_failed"] = m.delayed_and_failed(cp_list, cfg.get("delay_threshold_s", 5.0))
    else:
        res["vulnerability_s"] = None; res["delayed_failed"] = None
    res["_committed"], res["_checkpoints"] = committed, cp_list   # for charts

    outs = []
    starts = {e.get("label"): e for e in ev if e["event_type"] == "outage_start"}
    for e in ev:
        if e["event_type"] == "outage_end":
            s = starts.get(e.get("label"))
            if not s: continue
            o = {"label": e.get("label"), "mode": e.get("mode"), "planned": [e["planned_start_s"], e["planned_end_s"]],
                 "observed_start_s": sec(s), "observed_end_s": sec(e), "observed_duration_s": e["observed_duration_s"]}
            if committed and cp_list:
                o["max_confirmed_age_s"] = m.max_confirmed_age_during(cp_list, o["observed_start_s"], o["observed_end_s"])
                o["recovery_lag_s"] = m.recovery_lag(o["observed_end_s"], cp_list, committed)
            else:
                o["max_confirmed_age_s"] = o["recovery_lag_s"] = None
            outs.append(o)
    res["outages"] = outs
    res["tamper_results"] = [{k: e.get(k) for k in ("scenario", "expected", "observed", "verifier_source",
                                                    "matches_expectation", "note")}
                             for e in ev if e["event_type"] == "verification_result" and "scenario" in e]
    return res


def public(res: dict) -> dict:
    return {k: v for k, v in res.items() if not k.startswith("_")}


def analyze_all(raw_root: Path, out_dir: Path) -> list[dict]:
    out_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for d in sorted(p for p in Path(raw_root).iterdir() if p.is_dir() and (p / "events.jsonl").exists()):
        r = analyze_run(d); results.append(r)
        (out_dir / f"{d.name}.summary.json").write_text(json.dumps(public(r), indent=2, sort_keys=True) + "\n")
    return results


def main(argv=None):
    ap = argparse.ArgumentParser(description="Regenerate summaries, charts and dashboard from raw data")
    ap.add_argument("--raw-root", default="research/raw"); ap.add_argument("--out", default="research/reports")
    a = ap.parse_args(argv)
    from .dashboard import build_dashboard
    results = analyze_all(Path(a.raw_root), Path(a.out))
    build_dashboard(results, Path(a.out))
    print(f"analysed {len(results)} run(s) -> {a.out}/index.html")


if __name__ == "__main__":
    main()
