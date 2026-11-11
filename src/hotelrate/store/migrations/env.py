"""Alembic environment for the rate-shop store. Run through `hotelrate-mcp db upgrade`, not the alembic CLI."""

from __future__ import annotations

from alembic import context
from sqlalchemy import engine_from_config, pool

from hotelrate.store.models import Base

config = context.config
target_metadata = Base.metadata


def run_migrations() -> None:
    connection = config.attributes.get("connection")
    if connection is not None:
        _run(connection)
        return
    engine = engine_from_config(config.get_section(config.config_ini_section) or {}, prefix="sqlalchemy.",
                                poolclass=pool.NullPool)
    with engine.connect() as connection:
        _run(connection)


def _run(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=connection.dialect.name == "sqlite",
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    raise RuntimeError("Offline migrations are not supported; run `hotelrate-mcp db upgrade`.")
run_migrations()
