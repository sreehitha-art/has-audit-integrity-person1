"""Safe tamper runner (Phase 5).

Hard guards (checked before ANY destructive callable runs):
  * environment RESEARCH_MODE == "1"
  * target database name is EXACTLY "hms_research"
The runner mutates only through an injected ``Mutator`` and judges results only via an
injected ``Verifier`` adapter. It contains NO verifier of its own.

Until Person 1's verifier exists, ``MockVerifier`` is used and every result is labelled
``source: "mock"`` - those are interface tests, not HMS verification results.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field, asdict
from typing import Protocol

REQUIRED_DB = "hms_research"


class UnsafeTargetError(RuntimeError):
    pass


def assert_safe_target(db_name: str, env=None) -> None:
    env = os.environ if env is None else env
    if env.get("RESEARCH_MODE") != "1":
        raise UnsafeTargetError("refused: RESEARCH_MODE=1 is required")
    if db_name != REQUIRED_DB:           # exact match: no prefixes, no case-folding, no whitespace
        raise UnsafeTargetError(f"refused: target database must be exactly {REQUIRED_DB!r}, got {db_name!r}")


@dataclass(frozen=True)
class Scenario:
    name: str
    description: str
    expected_in_anchored_scope: str          # verified | mismatch | inconclusive | undetected_by_design | conditional
    note: str = ""


SCENARIOS = {s.name: s for s in [
    Scenario("modify_payload", "Modify an audit-event payload", "mismatch"),
    Scenario("delete_event", "Delete an audit event", "mismatch"),
    Scenario("forge_event_recomputed_hash", "Insert forged event with recomputed hash", "mismatch",
             "Only if the forged event lies within anchored history."),
    Scenario("swap_sequence_numbers", "Swap two events' sequence numbers", "mismatch"),
    Scenario("rewrite_unanchored_suffix", "Rewrite unanchored suffix and recompute hashes",
             "undetected_by_design", "Expected limitation: a valid result covers only the anchored prefix."),
    Scenario("rewrite_anchored_range", "Rewrite an already anchored range and recompute hashes", "mismatch"),
    Scenario("edit_business_row", "Directly edit a business row", "conditional",
             "Detected only if the agreed verifier implements business-row reconciliation."),
    Scenario("anchor_unavailable", "Verify while the anchor is unavailable", "inconclusive",
             "Unavailable anchor evidence must never be reported as verified."),
]}


class Mutator(Protocol):
    def apply(self, scenario: str, db_name: str) -> dict: ...        # returns details of the mutation


class Verifier(Protocol):
    source: str                                                        # "mock" | "hms"
    def verify(self, scope: str = "full") -> dict: ...                 # {"outcome": verified|mismatch|inconclusive, ...}


class MockVerifier:
    """Interface-test double. Returns whatever it was configured to return."""
    source = "mock"
    def __init__(self, outcomes: dict | None = None, default: str = "inconclusive"):
        self.outcomes, self.default, self.current = outcomes or {}, default, None
    def verify(self, scope="full"):
        return {"outcome": self.outcomes.get(self.current, self.default), "scope": scope}




class HmsVerifierAdapter:
    """PROVISIONAL interface to Person 1's verifier (POST /api/verification-runs, then
    GET /api/verification-runs/{id}). Implement after integration; do NOT re-implement
    verification logic here."""
    source = "hms"
    def verify(self, scope="full"):
        raise NotImplementedError("Wire to Person 1's verification API after Gate G2")


@dataclass
class TamperResult:
    scenario: str
    expected: str
    observed: str | None
    verifier_source: str
    matches_expectation: bool | None
    mutation: dict = field(default_factory=dict)
    note: str = ""


def run_scenario(name: str, db_name: str, mutator: Mutator, verifier: Verifier,
                 telemetry=None, scope: str = "full", env=None) -> TamperResult:
    assert_safe_target(db_name, env)                         # BEFORE any mutation
    sc = SCENARIOS[name]
    mutation = mutator.apply(name, db_name)
    if telemetry:
        telemetry.emit("tamper_applied", scenario=name, db=db_name, **{"mutation": mutation})
    if hasattr(verifier, "current"):
        verifier.current = name
    observed = verifier.verify(scope)["outcome"]
    exp = sc.expected_in_anchored_scope
    if exp == "undetected_by_design":
        ok = observed in ("verified", "mismatch")            # verified is the expected limitation
        note = sc.note
    elif exp == "conditional":
        ok = None                                           # cannot be judged without agreed verifier scope
        note = sc.note
    else:
        ok = observed == exp
        note = sc.note
    if observed == "verified" and exp in ("mismatch", "inconclusive"):
        ok = False                                          # false 'verified' is always a failure
    res = TamperResult(name, exp, observed, verifier.source, ok, mutation, note)
    if telemetry:
        telemetry.emit("verification_result", **{k: v for k, v in asdict(res).items() if k != "mutation"})
    return res
