"""R8: canonical data foundation — season type on statistics, provider id crosswalk, media rights

Revision ID: a9d4c6e18b53
Revises: f2c8b41d6a05
Create Date: 2026-10-18 06:00:00.000000

Three additive changes, none destructive:

1. ``player_season_stats.season_type`` / ``team_season_stats.season_type``. A statistic's
   identity includes whether it aggregates regular-season or playoff games; the old unique
   key (player, season, stat_type) could not hold both and nothing recorded which a row was.
   Existing rows are backfilled to ``regular`` — every row ingested so far came from NBA.com
   calls that default to the regular season, and the user CSV's games-played equals the
   regular-season row's for all 573 players. The unique constraint is rebuilt to include
   the column; no row is dropped or changed.

2. ``provider_entity_ids``: the crosswalk from a non-NBA.com provider's player/team id to
   Pivot's internal id, with the method and evidence of each link.

3. ``media_assets`` usage/licence columns. ``usage_status`` defaults to ``unverified`` for
   every existing row: a file being servable says nothing about the right to show it.
"""

import sqlalchemy as sa
from alembic import op

revision = "a9d4c6e18b53"
down_revision = "f2c8b41d6a05"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("player_season_stats") as batch:
        batch.add_column(
            sa.Column("season_type", sa.String(12), nullable=False, server_default="regular")
        )
        batch.drop_constraint("uq_pss", type_="unique")
        batch.create_unique_constraint(
            "uq_pss_scope", ["player_id", "season", "season_type", "stat_type"]
        )
    with op.batch_alter_table("team_season_stats") as batch:
        batch.add_column(
            sa.Column("season_type", sa.String(12), nullable=False, server_default="regular")
        )
        batch.drop_constraint("uq_tss", type_="unique")
        batch.create_unique_constraint(
            "uq_tss_scope", ["team_id", "season", "season_type", "stat_type"]
        )

    op.create_table(
        "provider_entity_ids",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("entity_type", sa.String(10), nullable=False),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("provider_id", sa.String(100), nullable=False),
        sa.Column("internal_id", sa.String(36), nullable=False),
        sa.Column("match_method", sa.String(40), nullable=False),
        sa.Column("confidence", sa.String(10), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("entity_type", "provider", "provider_id", name="uq_provider_entity"),
    )
    op.create_index(
        "ix_provider_entity_internal", "provider_entity_ids", ["entity_type", "internal_id"]
    )

    with op.batch_alter_table("media_assets") as batch:
        batch.add_column(
            sa.Column("usage_status", sa.String(20), nullable=False, server_default="unverified")
        )
        batch.add_column(sa.Column("license_ref", sa.String(300), nullable=True))
        batch.add_column(sa.Column("source_url", sa.String(500), nullable=True))
        batch.add_column(sa.Column("width", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("height", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("media_assets") as batch:
        for column in ("height", "width", "source_url", "license_ref", "usage_status"):
            batch.drop_column(column)

    op.drop_index("ix_provider_entity_internal", table_name="provider_entity_ids")
    op.drop_table("provider_entity_ids")

    # Rebuilding the old key fails if both a regular and a playoff row exist for one
    # (player, season, stat_type) — which is exactly the data the new key permits. Refuse
    # loudly rather than delete either.
    with op.batch_alter_table("team_season_stats") as batch:
        batch.drop_constraint("uq_tss_scope", type_="unique")
        batch.create_unique_constraint("uq_tss", ["team_id", "season", "stat_type"])
        batch.drop_column("season_type")
    with op.batch_alter_table("player_season_stats") as batch:
        batch.drop_constraint("uq_pss_scope", type_="unique")
        batch.create_unique_constraint("uq_pss", ["player_id", "season", "stat_type"])
        batch.drop_column("season_type")
