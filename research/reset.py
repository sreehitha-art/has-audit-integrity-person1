"""Safe reset of research state between runs.

Allowed targets only:
  * HMS side : database named EXACTLY 'hms_research' (re-created from 'hms_template')
  * Anchor   : database named EXACTLY 'anchor_research' (TRUNCATE retained_checkpoints)
'hms_dev' and 'anchor_dev' can never be reset by this module. Statements are returned /
executed through an injected callable so the safety logic is testable without PostgreSQL.
"""
from __future__ import annotations
import os
from typing import Callable

from .tampering import assert_safe_target, UnsafeTargetError

ANCHOR_RESEARCH_DB = "anchor_research"
HMS_TEMPLATE_DB = "hms_template"
Execute = Callable[[str, str], None]     # (database_to_connect_to, sql)


def _need_research_mode(env):
    if (os.environ if env is None else env).get("RESEARCH_MODE") != "1":
        raise UnsafeTargetError("refused: RESEARCH_MODE=1 is required")


def reset_hms_research(execute: Execute, db_name: str = "hms_research", env=None) -> list[str]:
    assert_safe_target(db_name, env)
    stmts = [f'DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE)',
             f'CREATE DATABASE "{db_name}" TEMPLATE "{HMS_TEMPLATE_DB}"']
    for s in stmts: execute("postgres", s)       # maintenance DB; names are validated constants
    return stmts


def reset_anchor_research(execute: Execute, db_name: str = ANCHOR_RESEARCH_DB, env=None) -> list[str]:
    _need_research_mode(env)
    if db_name != ANCHOR_RESEARCH_DB:
        raise UnsafeTargetError(f"refused: anchor reset only allowed on {ANCHOR_RESEARCH_DB!r}, got {db_name!r}")
    stmt = "TRUNCATE TABLE retained_checkpoints"
    execute(db_name, stmt)
    return [stmt]
