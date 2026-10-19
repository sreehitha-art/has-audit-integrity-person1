-- Reference DDL (the authoritative schema is the Alembic migration; keep in sync).
CREATE TABLE retained_checkpoints (
  anchor_id                  varchar(36) PRIMARY KEY,
  chain_id                   varchar(200) NOT NULL,
  checkpoint_sequence        bigint NOT NULL,
  terminal_event_sequence    bigint NOT NULL,
  terminal_event_hash        varchar(64) NOT NULL,
  previous_checkpoint_digest varchar(64),
  checkpoint_digest          varchar(64) NOT NULL,
  canonical_checkpoint       text NOT NULL,
  signature                  text NOT NULL,
  key_id                     varchar(200) NOT NULL,
  received_at                timestamptz NOT NULL,
  durably_retained_at        timestamptz NOT NULL DEFAULT clock_timestamp(),
  idempotency_key            varchar(200) NOT NULL,
  CONSTRAINT uq_chain_seq UNIQUE (chain_id, checkpoint_sequence),
  CONSTRAINT uq_idempotency_key UNIQUE (idempotency_key)
);
