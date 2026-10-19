"""create retained_checkpoints

Revision ID: 0001
Revises:
"""
from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "retained_checkpoints",
        sa.Column("anchor_id", sa.String(36), primary_key=True),
        sa.Column("chain_id", sa.String(200), nullable=False),
        sa.Column("checkpoint_sequence", sa.BigInteger, nullable=False),
        sa.Column("terminal_event_sequence", sa.BigInteger, nullable=False),
        sa.Column("terminal_event_hash", sa.String(64), nullable=False),
        sa.Column("previous_checkpoint_digest", sa.String(64), nullable=True),
        sa.Column("checkpoint_digest", sa.String(64), nullable=False),
        sa.Column("canonical_checkpoint", sa.Text, nullable=False),
        sa.Column("signature", sa.Text, nullable=False),
        sa.Column("key_id", sa.String(200), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("durably_retained_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("clock_timestamp()")),
        sa.Column("idempotency_key", sa.String(200), nullable=False),
        sa.UniqueConstraint("chain_id", "checkpoint_sequence", name="uq_chain_seq"),
        sa.UniqueConstraint("idempotency_key", name="uq_idempotency_key"),
    )


def downgrade():
    op.drop_table("retained_checkpoints")
