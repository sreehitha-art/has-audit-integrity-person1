"""Run every suite and print counts + the reason for every skip. Exit code 1 on any failure/error.
  python scripts/run_all_tests.py            (add --strict to also fail when anything is skipped)"""
import collections, sys, unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT))
strict = "--strict" in sys.argv
tot = collections.Counter(); reasons = collections.Counter(); bad = False
for name in ("anchor_service/tests", "research/tests"):
    suite = unittest.defaultTestLoader.discover(str(ROOT / name), top_level_dir=str(ROOT))
    r = unittest.TextTestRunner(verbosity=0, stream=open("/dev/null", "w")).run(suite)
    passed = r.testsRun - len(r.failures) - len(r.errors) - len(r.skipped)
    print(f"{name:22} run={r.testsRun:3} passed={passed:3} failed={len(r.failures):2} errors={len(r.errors):2} skipped={len(r.skipped):2}")
    for t, tb in r.failures + r.errors: print("  !!", t.id(), "\n", tb[-400:]); bad = True
    for t, why in r.skipped: reasons[why] += 1
    tot.update(run=r.testsRun, passed=passed, failed=len(r.failures), errors=len(r.errors), skipped=len(r.skipped))
print(f"TOTAL run={tot['run']} passed={tot['passed']} failed={tot['failed']} errors={tot['errors']} skipped={tot['skipped']}")
for why, n in reasons.items(): print(f"  skipped x{n}: {why}")
sys.exit(1 if bad or (strict and tot["skipped"]) else 0)
