"""Initial rate-shop store: hotels, studies, events, runs, the fetch ledger, observations and raw answers.

Revision ID: 0001
Revises:
Create Date: 2026-11-11
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

BigId = sa.BigInteger().with_variant(sa.Integer(), "sqlite")
Money = sa.Numeric(12, 2, asdecimal=False)
TZ = sa.DateTime(timezone=True)

APPEND_ONLY_MESSAGE = "rate_observations is append-only"


def upgrade() -> None:
    op.create_table(
        "hotels",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("collector", sa.String(40), nullable=False),
        sa.Column("source_id", sa.String(255), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("address", sa.String(500)),
        sa.Column("city", sa.String(120)),
        sa.Column("country", sa.String(80)),
        sa.Column("star_class", sa.Integer()),
        sa.Column("rating", sa.Float()),
        sa.Column("reviews_count", sa.Integer()),
        sa.Column("latitude", sa.Float()),
        sa.Column("longitude", sa.Float()),
        sa.Column("google_url", sa.Text()),
        sa.Column("created_at", TZ, nullable=False),
        sa.UniqueConstraint("collector", "source_id", name="uq_hotels_collector_source_id"),
    )
    op.create_table(
        "studies",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("city", sa.String(120), nullable=False),
        sa.Column("country", sa.String(80)),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("adults", sa.Integer(), nullable=False),
        sa.Column("calendar_days", sa.Integer(), nullable=False),
        sa.Column("calendar_nights", sa.JSON(), nullable=False),
        sa.Column("offers_nights", sa.Integer(), nullable=False),
        sa.Column("offers_months", sa.Integer(), nullable=False),
        sa.Column("schedule", sa.String(10), nullable=False),
        sa.Column("schedule_end_date", sa.Date()),
        sa.Column("collector_location", sa.String(40)),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("created_by", sa.String(200)),
        sa.Column("created_at", TZ, nullable=False),
        sa.UniqueConstraint("name", name="uq_studies_name"),
        sa.CheckConstraint("schedule IN ('none', 'daily', 'weekly')", name="ck_studies_schedule"),
    )
    op.create_table(
        "study_hotels",
        sa.Column("study_id", sa.Integer(), sa.ForeignKey("studies.id", name="fk_study_hotels_study_id_studies",
                                                          ondelete="CASCADE"), primary_key=True),
        sa.Column("hotel_id", sa.Integer(), sa.ForeignKey("hotels.id", name="fk_study_hotels_hotel_id_hotels"),
                  primary_key=True),
        sa.Column("role", sa.String(10), nullable=False),
        sa.Column("input_name", sa.String(255)),
        sa.Column("confirmed_by", sa.String(200)),
        sa.Column("confirmed_at", TZ, nullable=False),
        sa.CheckConstraint("role IN ('target', 'comp')", name="ck_study_hotels_role"),
    )
    op.create_table(
        "events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("city", sa.String(120), nullable=False),
        sa.Column("country", sa.String(80)),
        sa.Column("event_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date()),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("created_by", sa.String(200)),
        sa.Column("created_at", TZ, nullable=False),
        sa.UniqueConstraint("city", "event_date", "name", name="uq_events_city_event_date_name"),
    )
    op.create_table(
        "runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("study_id", sa.Integer(), sa.ForeignKey("studies.id", name="fk_runs_study_id_studies",
                                                          ondelete="CASCADE"), nullable=False),
        sa.Column("trigger", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("location", sa.String(40)),
        sa.Column("collector_version", sa.String(60), nullable=False),
        sa.Column("requested_at", TZ, nullable=False),
        sa.Column("started_at", TZ),
        sa.Column("finished_at", TZ),
        sa.Column("requests_sent", sa.Integer(), nullable=False),
        sa.Column("counts", sa.JSON(), nullable=False),
        sa.Column("error", sa.Text()),
        sa.CheckConstraint("status IN ('running', 'succeeded', 'partial', 'failed')", name="ck_runs_status"),
    )
    op.create_index("ix_runs_study_id", "runs", ["study_id"])
    op.create_table(
        "fetch_ledger",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("fetch_key", sa.String(400), nullable=False),
        sa.Column("collector", sa.String(40), nullable=False),
        sa.Column("collector_version", sa.String(60), nullable=False),
        sa.Column("hotel_id", sa.Integer(), sa.ForeignKey("hotels.id", name="fk_fetch_ledger_hotel_id_hotels"),
                  nullable=False),
        sa.Column("task", sa.String(20), nullable=False),
        sa.Column("check_in", sa.Date(), nullable=False),
        sa.Column("nights", sa.Integer(), nullable=False),
        sa.Column("adults", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("calendar_days", sa.Integer()),
        sa.Column("fetch_date", sa.Date(), nullable=False),
        sa.Column("run_id", sa.Integer(), sa.ForeignKey("runs.id", name="fk_fetch_ledger_run_id_runs",
                                                        ondelete="SET NULL")),
        sa.Column("outcome", sa.String(20), nullable=False),
        sa.Column("error", sa.Text()),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("requests", sa.Integer(), nullable=False),
        sa.Column("claimed_at", TZ, nullable=False),
        sa.Column("fetched_at", TZ),
        sa.UniqueConstraint("fetch_key", name="uq_fetch_ledger_fetch_key"),
        sa.CheckConstraint("task IN ('calendar', 'offers')", name="ck_fetch_ledger_task"),
        sa.CheckConstraint(
            "outcome IN ('pending', 'ok', 'blocked', 'not_found', 'parse_error', 'failed')",
            name="ck_fetch_ledger_outcome",
        ),
    )
    op.create_index("ix_fetch_ledger_hotel_task_date", "fetch_ledger", ["hotel_id", "task", "fetch_date"])
    op.create_table(
        "run_fetches",
        sa.Column("run_id", sa.Integer(), sa.ForeignKey("runs.id", name="fk_run_fetches_run_id_runs",
                                                        ondelete="CASCADE"), primary_key=True),
        sa.Column("fetch_id", sa.Integer(), sa.ForeignKey("fetch_ledger.id",
                                                          name="fk_run_fetches_fetch_id_fetch_ledger"),
                  primary_key=True),
        sa.Column("reused", sa.Boolean(), nullable=False),
    )
    op.create_table(
        "rate_observations",
        sa.Column("id", BigId, primary_key=True),
        sa.Column("hotel_id", sa.Integer(), sa.ForeignKey("hotels.id", name="fk_rate_observations_hotel_id_hotels"),
                  nullable=False),
        sa.Column("fetch_id", sa.Integer(), sa.ForeignKey("fetch_ledger.id",
                                                          name="fk_rate_observations_fetch_id_fetch_ledger"),
                  nullable=False),
        sa.Column("source_task", sa.String(20), nullable=False),
        sa.Column("check_in", sa.Date(), nullable=False),
        sa.Column("nights", sa.Integer(), nullable=False),
        sa.Column("adults", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("available", sa.Boolean(), nullable=False),
        sa.Column("day_status", sa.String(20)),
        sa.Column("channel", sa.String(200)),
        sa.Column("is_official_site", sa.Boolean()),
        sa.Column("is_sponsored", sa.Boolean()),
        sa.Column("room", sa.String(500)),
        sa.Column("rate_plan", sa.String(120)),
        sa.Column("free_cancellation", sa.Boolean()),
        sa.Column("cancellation_deadline", sa.String(60)),
        sa.Column("breakfast", sa.Boolean()),
        sa.Column("is_member_rate", sa.Boolean()),
        sa.Column("price_per_night", Money),
        sa.Column("price_total", Money),
        sa.Column("total_before_taxes", Money),
        sa.Column("taxes", Money),
        sa.Column("observed_at", TZ, nullable=False),
    )
    op.create_index(
        "ix_rate_observations_hotel_task_check_in", "rate_observations", ["hotel_id", "source_task", "check_in"]
    )
    op.create_index("ix_rate_observations_fetch_id", "rate_observations", ["fetch_id"])
    op.create_table(
        "raw_responses",
        sa.Column("id", BigId, primary_key=True),
        sa.Column("fetch_id", sa.Integer(), sa.ForeignKey("fetch_ledger.id",
                                                          name="fk_raw_responses_fetch_id_fetch_ledger")),
        sa.Column("collector_version", sa.String(60), nullable=False),
        sa.Column("rpc_id", sa.String(40), nullable=False),
        sa.Column("request", sa.Text(), nullable=False),
        sa.Column("http_status", sa.Integer()),
        sa.Column("problem", sa.Text()),
        sa.Column("body_gzip", sa.LargeBinary()),
        sa.Column("captured_at", TZ, nullable=False),
    )
    op.create_index("ix_raw_responses_fetch_id", "raw_responses", ["fetch_id"])
    _create_append_only_guard()


def downgrade() -> None:
    _drop_append_only_guard()
    for table in ("raw_responses", "rate_observations", "run_fetches", "fetch_ledger", "runs", "events",
                  "study_hotels", "studies", "hotels"):
        op.drop_table(table)


def _create_append_only_guard() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "sqlite":
        for action in ("UPDATE", "DELETE"):
            op.execute(
                f"CREATE TRIGGER rate_observations_no_{action.lower()} BEFORE {action} ON rate_observations "
                f"BEGIN SELECT RAISE(ABORT, '{APPEND_ONLY_MESSAGE}'); END"
            )
    elif dialect == "postgresql":
        op.execute(
            "CREATE FUNCTION rate_observations_append_only() RETURNS trigger LANGUAGE plpgsql AS $$ "
            f"BEGIN RAISE EXCEPTION '{APPEND_ONLY_MESSAGE}'; END $$"
        )
        op.execute(
            "CREATE TRIGGER rate_observations_append_only BEFORE UPDATE OR DELETE ON rate_observations "
            "FOR EACH ROW EXECUTE FUNCTION rate_observations_append_only()"
        )
        # TRUNCATE skips row triggers, so it gets its own statement trigger.
        op.execute(
            "CREATE TRIGGER rate_observations_no_truncate BEFORE TRUNCATE ON rate_observations "
            "FOR EACH STATEMENT EXECUTE FUNCTION rate_observations_append_only()"
        )
    else:
        raise NotImplementedError(f"No append-only guard for the {dialect} database")


def _drop_append_only_guard() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "sqlite":
        op.execute("DROP TRIGGER IF EXISTS rate_observations_no_update")
        op.execute("DROP TRIGGER IF EXISTS rate_observations_no_delete")
    elif dialect == "postgresql":
        op.execute("DROP TRIGGER IF EXISTS rate_observations_no_truncate ON rate_observations")
        op.execute("DROP TRIGGER IF EXISTS rate_observations_append_only ON rate_observations")
        op.execute("DROP FUNCTION IF EXISTS rate_observations_append_only()")
