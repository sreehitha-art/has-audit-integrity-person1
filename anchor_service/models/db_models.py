from sqlalchemy import BigInteger, Column, DateTime, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class RetainedCheckpoint(Base):
    __tablename__ = "retained_checkpoints"
    anchor_id = Column(String(36), primary_key=True)
    chain_id = Column(String(200), nullable=False)
    checkpoint_sequence = Column(BigInteger, nullable=False)
    terminal_event_sequence = Column(BigInteger, nullable=False)
    terminal_event_hash = Column(String(64), nullable=False)
    previous_checkpoint_digest = Column(String(64), nullable=True)
    checkpoint_digest = Column(String(64), nullable=False)
    canonical_checkpoint = Column(Text, nullable=False)
    signature = Column(Text, nullable=False)
    key_id = Column(String(200), nullable=False)
    received_at = Column(DateTime(timezone=True), nullable=False)
    # DB clock_timestamp() evaluated at the INSERT statement inside the committing transaction.
    # It is a LOWER BOUND on commit completion (commit finishes at/after it), not a post-commit
    # reading. See docs/anchor_api_contract.md -> 'durably_retained_at semantics'.
    durably_retained_at = Column(DateTime(timezone=True), nullable=False, server_default=func.clock_timestamp())
    idempotency_key = Column(String(200), nullable=False)
    __table_args__ = (
        UniqueConstraint("chain_id", "checkpoint_sequence", name="uq_chain_seq"),
        # SCOPE: idempotency keys are globally unique across all chains (documented choice).
        UniqueConstraint("idempotency_key", name="uq_idempotency_key"),
    )
