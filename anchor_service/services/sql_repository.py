"""SQLAlchemy/PostgreSQL implementation of the core Repository. UNTESTED in the build
sandbox (no PostgreSQL there) - run anchor_service/tests/test_api_postgres.py locally."""
from __future__ import annotations
from datetime import datetime
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from ..models.db_models import RetainedCheckpoint as M
from .core import Conflict, Record


def _iso(dt) -> str:
    if dt is None: return ""
    if isinstance(dt, str): return dt
    return dt.astimezone(__import__("datetime").timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _parse(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _to_record(m: M) -> Record:
    return Record(m.anchor_id, m.chain_id, m.checkpoint_sequence, m.terminal_event_sequence,
                  m.terminal_event_hash, m.previous_checkpoint_digest, m.checkpoint_digest,
                  m.canonical_checkpoint, m.signature, m.key_id, _iso(m.received_at),
                  _iso(m.durably_retained_at), m.idempotency_key)


class SqlRepository:
    def __init__(self, session_factory):
        self.sf = session_factory

    def _one(self, stmt):
        with self.sf() as s:
            m = s.execute(stmt).scalars().first()
            return _to_record(m) if m else None

    def get_by_idempotency_key(self, key): return self._one(select(M).where(M.idempotency_key == key))
    def get_by_chain_seq(self, c, q): return self._one(select(M).where(M.chain_id == c, M.checkpoint_sequence == q))
    def get_by_id(self, anchor_id): return self._one(select(M).where(M.anchor_id == anchor_id))
    def last_in_chain(self, c):
        return self._one(select(M).where(M.chain_id == c).order_by(M.checkpoint_sequence.desc()).limit(1))

    def list_chain(self, c):
        with self.sf() as s:
            return [_to_record(m) for m in s.execute(
                select(M).where(M.chain_id == c).order_by(M.checkpoint_sequence)).scalars()]

    def insert(self, rec: Record) -> Record:
        """INSERT + COMMIT; returns only after commit succeeded. durably_retained_at is the
        DB clock_timestamp() during the transaction, read back via RETURNING."""
        with self.sf() as s:
            m = M(anchor_id=rec.anchor_id, chain_id=rec.chain_id, checkpoint_sequence=rec.checkpoint_sequence,
                  terminal_event_sequence=rec.terminal_event_sequence, terminal_event_hash=rec.terminal_event_hash,
                  previous_checkpoint_digest=rec.previous_checkpoint_digest, checkpoint_digest=rec.checkpoint_digest,
                  canonical_checkpoint=rec.canonical_checkpoint, signature=rec.signature, key_id=rec.key_id,
                  received_at=_parse(rec.received_at), idempotency_key=rec.idempotency_key)
            try:
                s.add(m); s.flush()
                s.commit()                       # durable here; exceptions propagate => no receipt
            except IntegrityError as exc:
                s.rollback()
                raise Conflict("uniqueness constraint violated (chain/sequence or idempotency key)") from exc
            s.refresh(m)
            return _to_record(m)

    def count(self):
        with self.sf() as s: return s.execute(text("SELECT count(*) FROM retained_checkpoints")).scalar_one()

    def ping(self):
        try:
            with self.sf() as s: s.execute(text("SELECT 1")); return True
        except Exception: return False
