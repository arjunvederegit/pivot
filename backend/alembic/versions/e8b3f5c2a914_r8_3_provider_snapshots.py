"""R8.3: keep cap hit / base salary / cash apart, injury return dates, provider statistics

Revision ID: e8b3f5c2a914
Revises: c5e2b7d91f34
Create Date: 2026-10-18 23:30:00.000000

Additive only. Nothing is dropped, rewritten or backfilled, so downgrade is a plain drop.

* ``contract_years.base_salary`` / ``total_cash`` / ``salary_basis`` — BALLDONTLIE reports three
  different dollar figures per contract year (observed live: they differ for ~12 % of rows).
  ``salary`` keeps its meaning to every existing reader; ``salary_basis`` says which provider
  figure it holds (``cap_hit``), and the other two are stored beside it rather than conflated.
* ``injuries.return_date`` — the provider's expected return date, kept as a date, not prose.
* ``provider_stat_snapshots`` — a provider's statistics for one player/team and one scope, in the
  provider's own vocabulary. ``player_season_stats`` is keyed by NBA.com stat types and has no
  provider in its unique key, so a BALLDONTLIE row written there would either collide with or
  silently replace an NBA.com row. This table cannot.
"""
import sqlalchemy as sa
from alembic import op

revision = "e8b3f5c2a914"
down_revision = "c5e2b7d91f34"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("contract_years") as batch:
        batch.add_column(sa.Column("base_salary", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("total_cash", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("salary_basis", sa.String(20), nullable=True))
    with op.batch_alter_table("injuries") as batch:
        batch.add_column(sa.Column("return_date", sa.Date(), nullable=True))
    op.create_table(
        "provider_stat_snapshots",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("entity_type", sa.String(10), nullable=False),
        sa.Column("internal_id", sa.String(36), nullable=False),
        sa.Column("season", sa.String(10), nullable=False),
        sa.Column("season_type", sa.String(12), nullable=False),
        sa.Column("stat_group", sa.String(40), nullable=False),
        sa.Column("aggregation", sa.String(20), nullable=False),
        sa.Column("basis", sa.String(12), nullable=False, server_default="observed"),
        sa.Column("games_played", sa.Integer(), nullable=True),
        sa.Column("values", sa.JSON(), nullable=False),
        sa.Column("source_record_id", sa.String(100), nullable=True),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ingestion_run_id", sa.String(36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "provider",
            "entity_type",
            "internal_id",
            "season",
            "season_type",
            "stat_group",
            name="uq_provider_stat_scope",
        ),
    )
    op.create_index(
        "ix_pss_provider_scope",
        "provider_stat_snapshots",
        ["provider", "entity_type", "season", "season_type", "stat_group"],
    )


def downgrade() -> None:
    op.drop_index("ix_pss_provider_scope", table_name="provider_stat_snapshots")
    op.drop_table("provider_stat_snapshots")
    with op.batch_alter_table("injuries") as batch:
        batch.drop_column("return_date")
    with op.batch_alter_table("contract_years") as batch:
        batch.drop_column("salary_basis")
        batch.drop_column("total_cash")
        batch.drop_column("base_salary")
