"""Environment-driven settings. No secrets in source: everything comes from env / .env."""
import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def _load_dotenv():
    p = ROOT.parent / ".env"
    if p.exists():
        for line in p.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1); os.environ.setdefault(k.strip(), v.strip())


@dataclass(frozen=True)
class Settings:
    database_url: str
    api_token: str
    test_mode: bool
    test_token: str
    trusted_keys_dir: str
    port: int
    raw_root: str | None
    run_id: str | None

    @staticmethod
    def load() -> "Settings":
        _load_dotenv()
        e = os.environ.get
        return Settings(
            database_url=e("ANCHOR_DATABASE_URL", "postgresql+psycopg2://anchor_user:CHANGE_ME@localhost:5432/anchor_dev"),
            api_token=e("ANCHOR_API_TOKEN", ""),
            test_mode=e("ANCHOR_TEST_MODE", "0") == "1",
            test_token=e("ANCHOR_TEST_TOKEN", ""),
            trusted_keys_dir=e("ANCHOR_TRUSTED_KEYS_DIR", str(ROOT / "trusted_keys")),
            port=int(e("ANCHOR_PORT", "8001")),
            raw_root=e("ANCHOR_RAW_ROOT") or None,
            run_id=e("ANCHOR_RUN_ID") or None,
        )
