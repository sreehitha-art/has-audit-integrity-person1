# HMS Tamper-Evident Audit Logs — Person 2 (Anchor & Research)

Independent anchoring service, telemetry, workloads, safe tampering harness, experiment runner, analysis, dashboard.
Synthetic data only. Branch: `p2/anchor-foundation`. Person 1's HMS is **not** modified or required.

## Setup (beginner friendly)
```bash
python3.12 -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env        # then edit the CHANGE_ME values
python scripts/generate_keys.py --key-id app-key-1 --out-private ../p2_private_keys   # TEST keys; keep private key OUT of git
```
Create the database, then migrate:
```bash
createdb anchor_dev                       # and a DB user for it; the HMS database/user must be different
alembic upgrade head
python -m anchor_service.main             # serves on http://127.0.0.1:8001
```
## Tests
```bash
python -m unittest discover -s anchor_service/tests -t . && python -m unittest discover -s research/tests -t .
# or: pytest        # API tests need fastapi+httpx; Postgres tests need ANCHOR_TEST_DATABASE_URL (see test file)
```
## Run things
- Mock harness run: `python -m research.scripts.run_experiment --config research/configs/smoke_mock.json --mode mock --time-scale 0.1`
- Workload only: `python -m research.scripts.run_workload --config CFG.json` (add `--base-url http://localhost:8000` for the real HMS adapter — provisional endpoints)
- Reports: `python -m research.analysis.analyze_runs`
- Fault control (test mode): `curl -X POST :8001/v1/_test/fault -H "X-Test-Token: $ANCHOR_TEST_TOKEN" -d '{"mode":"down"}'`

## What is real vs mock vs unverified
See `docs/STATUS.md`.

## Pushing
`git remote add origin <url> && git push -u origin p2/anchor-foundation` (nothing has been pushed). To integrate: open a PR into the shared integration branch; both of you approve changes to `docs/contracts.md`, `.env.example`, `README.md`.
