# Reproducibility
- Schedules: `numpy.random.default_rng(seed)`; same config ⇒ same `schedule_digest` (tested).
- Config digest: SHA-256 of canonical JSON, stored in every manifest.
- Manifest: git commit/dirty flag, requirements SHA-256, Python/OS/machine; PostgreSQL version field exists but is
  not filled yet (needs a DB connection in the real runner).
- Regenerate everything from raw data:
  `python -m research.analysis.analyze_runs --raw-root research/raw --out research/reports` → open `research/reports/index.html`.
  Output regeneration is byte-identical for the same raw data (tested).
- Raw data (`research/raw/`) and reports are git-ignored by default; archive intentionally-published synthetic runs separately.
