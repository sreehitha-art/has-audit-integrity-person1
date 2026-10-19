"""Run manifest: reproducibility metadata. Status is never 'success' unless every stage ran."""
from __future__ import annotations
import hashlib, json, platform, subprocess, sys
from pathlib import Path
from .telemetry import utc_now_iso

VALID_STATUS = {"running", "completed", "failed", "incomplete", "mock_completed"}


def _run(cmd, cwd=None):
    try:
        return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=10).stdout.strip() or None
    except Exception:
        return None


def collect_environment(repo_root: Path, requirements_file: str = "requirements.txt") -> dict:
    req = repo_root / requirements_file
    return {
        "git_commit": _run(["git", "rev-parse", "HEAD"], repo_root),
        "git_dirty": bool(_run(["git", "status", "--porcelain"], repo_root)),
        "requirements_sha256": hashlib.sha256(req.read_bytes()).hexdigest() if req.exists() else None,
        "python": sys.version.split()[0], "os": platform.platform(),
        "machine": {"arch": platform.machine(), "processor": platform.processor() or None,
                    "node": platform.node()},
        "postgres_version": None,   # filled by the runner when a DB connection exists
    }


def write_manifest(path: Path, data: dict) -> None:
    if data.get("status") not in VALID_STATUS:
        raise ValueError(f"invalid status {data.get('status')!r}")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True, default=str) + "\n")
    tmp.replace(path)          # atomic
