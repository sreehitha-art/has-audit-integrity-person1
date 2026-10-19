import os
from alembic import context
from sqlalchemy import create_engine
from anchor_service.models.db_models import Base
from anchor_service.config import Settings

config = context.config
target_metadata = Base.metadata
URL = os.environ.get("ANCHOR_DATABASE_URL") or Settings.load().database_url


def run_migrations_offline():
    context.configure(url=URL, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction(): context.run_migrations()


def run_migrations_online():
    engine = create_engine(URL)
    with engine.connect() as conn:
        context.configure(connection=conn, target_metadata=target_metadata)
        with context.begin_transaction(): context.run_migrations()


run_migrations_offline() if context.is_offline_mode() else run_migrations_online()
