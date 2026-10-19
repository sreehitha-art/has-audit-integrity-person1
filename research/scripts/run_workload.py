"""Run only the workload against a target (mock by default).
  python -m research.scripts.run_workload --config CFG.json [--base-url http://localhost:8000 --token-env HMS_TOKEN]"""
import argparse, asyncio, json, os
from pathlib import Path
from research.experiment_config import ExperimentConfig
from research.telemetry import Telemetry
from research.workload_client import MockHmsClient, HttpxHmsClient, run_workload

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--config", required=True)
    ap.add_argument("--base-url"); ap.add_argument("--token-env", default="HMS_TOKEN")
    ap.add_argument("--raw-root"); ap.add_argument("--time-scale", type=float, default=1.0)
    a = ap.parse_args(); cfg = ExperimentConfig.load(a.config)
    client = HttpxHmsClient(a.base_url, os.environ.get(a.token_env)) if a.base_url else MockHmsClient()
    with Telemetry(cfg.run_id, a.raw_root) as tel:
        print(json.dumps(asyncio.run(run_workload(cfg, client, tel, time_scale=a.time_scale)), indent=2))

if __name__ == "__main__":
    main()
