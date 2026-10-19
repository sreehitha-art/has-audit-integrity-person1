"""SQLite Repository: DEV/TEST STAND-IN ONLY. It is NOT PostgreSQL and passing tests against it is not
evidence about PostgreSQL behaviour (clock_timestamp(), isolation, fsync settings, psycopg2 errors).
It exists because it is a real transactional, file-backed database with the same unique constraints,
so durability-before-receipt, restart persistence, multi-worker races and commit failure can be
exercised without a PostgreSQL server.

One connection per call (no sharing across threads); WAL + synchronous=FULL."""
from __future__ import annotations
import sqlite3
from typing import Callable

from .core import Conflict, Record, now_iso

DDL = """
CREATE TABLE IF NOT EXISTS retained_checkpoints (
  anchor_id TEXT PRIMARY KEY,
  chain_id TEXT NOT NULL,
  checkpoint_sequence INTEGER NOT NULL,
  terminal_event_sequence INTEGER NOT NULL,
  terminal_event_hash TEXT NOT NULL,
  previous_checkpoint_digest TEXT,
  checkpoint_digest TEXT NOT NULL,
  canonical_checkpoint TEXT NOT NULL,
  signature TEXT NOT NULL,
  key_id TEXT NOT NULL,
  received_at TEXT NOT NULL,
  durably_retained_at TEXT NOT NULL,
  idempotency_key TEXT NOT NULL,
  CONSTRAINT uq_chain_seq UNIQUE (chain_id, checkpoint_sequence),
  CONSTRAINT uq_idempotency_key UNIQUE (idempotency_key)
)"""
COLS = ("anchor_id chain_id checkpoint_sequence terminal_event_sequence terminal_event_hash "
        "previous_checkpoint_digest checkpoint_digest canonical_checkpoint signature key_id "
        "received_at durably_retained_at idempotency_key").split()


class SqliteRepository:
    def __init__(self, path: str, commit_hook: Callable[[], None] | None = None):
        self.path = path
        self.commit_hook = commit_hook          # test seam: runs right BEFORE COMMIT (may raise)
        with self._conn() as c:
            c.execute("PRAGMA journal_mode=WAL"); c.execute(DDL)

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.path, timeout=30, isolation_level=None)   # explicit BEGIN/COMMIT
        c.execute("PRAGMA synchronous=FULL")
        return c

    def _one(self, where: str, args: tuple, order: str = "") -> Record | None:
        c = self._conn()
        try:
            row = c.execute(f"SELECT {','.join(COLS)} FROM retained_checkpoints WHERE {where} {order} LIMIT 1", args).fetchone()
        finally:
            c.close()
        return Record(*row) if row else None

    def get_by_idempotency_key(self, key): return self._one("idempotency_key=?", (key,))
    def get_by_chain_seq(self, chain_id, seq): return self._one("chain_id=? AND checkpoint_sequence=?", (chain_id, seq))
    def get_by_id(self, anchor_id): return self._one("anchor_id=?", (anchor_id,))
    def last_in_chain(self, chain_id): return self._one("chain_id=?", (chain_id,), "ORDER BY checkpoint_sequence DESC")

    def list_chain(self, chain_id):
        c = self._conn()
        try:
            rows = c.execute(f"SELECT {','.join(COLS)} FROM retained_checkpoints WHERE chain_id=? "
                             "ORDER BY checkpoint_sequence", (chain_id,)).fetchall()
        finally:
            c.close()
        return [Record(*r) for r in rows]

    def insert(self, rec: Record) -> Record:
        """INSERT inside a transaction; COMMIT; only then return. Any failure => rollback, exception, no receipt."""
        rec.durably_retained_at = now_iso()      # same semantics as the PG default: stamped at INSERT, lower bound on commit
        c = self._conn()
        try:
            c.execute("BEGIN IMMEDIATE")
            try:
                c.execute(f"INSERT INTO retained_checkpoints ({','.join(COLS)}) VALUES ({','.join('?' * len(COLS))})",
                          tuple(getattr(rec, k) for k in COLS))
                if self.commit_hook:
                    self.commit_hook()
                c.execute("COMMIT")
            except sqlite3.IntegrityError as exc:
                c.execute("ROLLBACK"); raise Conflict("uniqueness constraint violated (chain/sequence or idempotency key)") from exc
            except BaseException:
                try: c.execute("ROLLBACK")
                except sqlite3.Error: pass
                raise
        finally:
            c.close()
        return rec

    def count(self):
        c = self._conn()
        try: return c.execute("SELECT count(*) FROM retained_checkpoints").fetchone()[0]
        finally: c.close()

    def ping(self):
        try:
            c = self._conn(); c.execute("SELECT 1"); c.close(); return True
        except sqlite3.Error: return False
