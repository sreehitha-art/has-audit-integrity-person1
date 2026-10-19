"""Experiment runner.

  python -m research.scripts.run_experiment --config research/configs/smoke_mock.json --mode mock

--mode mock : mock HMS client + mock fault controller + mock verifier. Exercises the harness
              (config, telemetry, outage controller, manifest) ONLY. Manifest status is
              'mock_completed' and results are labelled mock - never real HMS data.
--mode real : requires Person 1's HMS + the anchor service; the HMS-specific stages
              (database reset from hms_template, service start, drain, verification) are NOT
              implemented yet and the run is recorded as 'incomplete' rather than faked.
"""
from __future__ import annotations
import argparse, asyncio, json, os, sys, threading, traceback
from pathlib import Path

from research.experiment_config import ExperimentConfig
from research.manifest import collect_environment, write_manifest
from research.outage import run_outage_schedule
from research.telemetry import Telemetry, utc_now_iso
from research.tampering import REQUIRED_DB
from research.workload import plan_schedule, schedule_stats
from research.workload_client import MockHmsClient, run_workload

REPO = Path(__file__).resolve().parents[2]


def run(cfg: ExperimentConfig, mode: str, raw_root: Path, time_scale: float = 1.0) -> dict:
    run_dir = raw_root / cfg.run_id
    tel = Telemetry(cfg.run_id, raw_root)
    manifest = {"run_id": cfg.run_id, "mode": mode, "seed": cfg.seed, "config_sha256": cfg.digest(),
                "status": "running", "started_utc": utc_now_iso(), "time_scale": time_scale,
                "environment": collect_environment(REPO), "stages": {},
                "paths": {"raw": str(run_dir), "events": str(tel.path)}}
    manifest_path = run_dir / "manifest.json"
    run_dir.mkdir(parents=True, exist_ok=True)
    cfg.save(run_dir / "config.json")
    write_manifest(manifest_path, manifest)
    tel.emit("experiment_start", seed=cfg.seed, config_sha256=cfg.digest(), mode=mode,
             policy=cfg.policy_name, workload=cfg.workload_type)
    try:
        sched = plan_schedule(cfg)
        manifest["schedule_stats"] = schedule_stats(cfg, sched)
        manifest["stages"]["plan"] = "ok"
        if mode == "real":
            if os.environ.get("RESEARCH_MODE") != "1":
                raise RuntimeError("refused: RESEARCH_MODE=1 required for real runs")
            raise NotImplementedError(
                f"real mode needs Person 1's HMS ({REQUIRED_DB} reset from hms_template, service start, "
                "drain, verifier). Not implemented until integration.")
        # ---- mock harness run ----
        observed = []
        faults = []
        def mock_fault(m, d): faults.append((m, d))
        th = threading.Thread(target=lambda: observed.extend(
            run_outage_schedule(cfg.outages, mock_fault, tel, time_scale)), daemon=True)
        th.start()
        summary = asyncio.run(run_workload(cfg, MockHmsClient(), tel, time_scale=time_scale, schedule=sched))
        th.join(timeout=cfg.duration_s * time_scale + 5)
        manifest["workload_summary"] = summary
        manifest["outages_observed"] = observed
        manifest["stages"].update({"workload": "ok", "outages": "ok", "verification": "skipped (mock)"})
        manifest["status"] = "mock_completed"
    except NotImplementedError as exc:
        manifest["status"] = "incomplete"; manifest["error"] = str(exc)
    except Exception as exc:
        manifest["status"] = "failed"; manifest["error"] = f"{type(exc).__name__}: {exc}"
        manifest["traceback"] = traceback.format_exc()
    finally:
        manifest["ended_utc"] = utc_now_iso()
        tel.emit("experiment_end", status=manifest["status"])
        tel.close()
        write_manifest(manifest_path, manifest)      # raw logs preserved either way
    return manifest


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True); ap.add_argument("--mode", choices=["mock", "real"], default="mock")
    ap.add_argument("--raw-root", default=str(REPO / "research" / "raw"))
    ap.add_argument("--time-scale", type=float, default=1.0)
    a = ap.parse_args(argv)
    m = run(ExperimentConfig.load(a.config), a.mode, Path(a.raw_root), a.time_scale)
    print(json.dumps({k: m[k] for k in ("run_id", "status", "mode") if k in m}) +
          (f"\n{m.get('error','')}" if m["status"] in ("failed", "incomplete") else ""))
    return 0 if m["status"] == "mock_completed" or m["status"] == "completed" else 1


if __name__ == "__main__":
    sys.exit(main())
