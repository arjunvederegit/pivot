"""R9.1: candidate model status, model lineage, and a separate candidate-estimate table

Revision ID: a7d3e1f9c204
Revises: e8b3f5c2a914
Create Date: 2026-10-19 12:00:00.000000

Additive. ``model_versions`` gains three columns and ``candidate_estimates`` is a new table.

* ``status`` is backfilled from ``is_active`` (``active`` / ``retired``) so every existing row has a
  defined state; nothing else about an existing row changes.
* ``config`` / ``lineage`` default to ``{}``: models trained before R9.1 simply have no recorded
  lineage, and nothing is invented for them.
* ``version`` is widened from 30 to 64 characters: candidate versions (``cand-<model>-<target>-<hash>``)
  are up to ~57 characters. SQLite never enforced the old length; PostgreSQL does, and would have
  rejected every candidate (the class of defect R8.1 found in ``algorithm``).
* ``candidate_estimates`` is not read by any serving path. It exists so that a model trained for
  evaluation has somewhere to keep its estimates that is not ``player_impact_estimates``.

Downgrade drops exactly what upgrade added, and **refuses to run while any candidate or rejected model
exists**: removing the columns would leave those rows looking like retired models with no estimates.
Delete the candidates deliberately first (they are experiment outputs, reproducible from their recorded
configuration and data snapshot), then downgrade.
"""
import sqlalchemy as sa

from alembic import op

revision = "a7d3e1f9c204"
down_revision = "e8b3f5c2a914"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("model_versions") as batch:
        batch.alter_column("version", existing_type=sa.String(30), type_=sa.String(64), existing_nullable=False)
        batch.add_column(
            sa.Column("status", sa.String(12), nullable=False, server_default="retired")
        )
        batch.add_column(sa.Column("config", sa.JSON(), nullable=False, server_default="{}"))
        batch.add_column(sa.Column("lineage", sa.JSON(), nullable=False, server_default="{}"))
    op.execute("UPDATE model_versions SET status = 'active' WHERE is_active")
    op.create_table(
        "candidate_estimates",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("model_version_id", sa.String(36), sa.ForeignKey("model_versions.id"), nullable=False),
        sa.Column("player_id", sa.String(36), sa.ForeignKey("players.id"), nullable=False),
        sa.Column("season", sa.String(10), nullable=False),
        sa.Column("target_season", sa.String(10), nullable=False),
        sa.Column("target", sa.String(40), nullable=False),
        sa.Column("output_kind", sa.String(10), nullable=False),
        sa.Column("estimate", sa.Float(), nullable=False),
        sa.Column("interval_low", sa.Float(), nullable=True),
        sa.Column("interval_high", sa.Float(), nullable=True),
        sa.Column("interval_level", sa.Float(), nullable=True),
        sa.Column("exposure", sa.Float(), nullable=True),
        sa.Column("inputs", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "model_version_id", "player_id", "season", "target", name="uq_candidate_estimate"
        ),
    )
    op.create_index("ix_candidate_estimates_version", "candidate_estimates", ["model_version_id"])
    op.create_index("ix_candidate_estimates_player_id", "candidate_estimates", ["player_id"])


def downgrade() -> None:
    bind = op.get_bind()
    held = bind.execute(
        sa.text("select count(*) from model_versions where status in ('candidate', 'rejected')")
    ).scalar_one()
    if held:
        raise RuntimeError(
            f"refusing to downgrade: {held} candidate/rejected model(s) exist and the downgrade would "
            "strip their status and estimates, leaving them looking like retired models. Delete them "
            "deliberately first (they can be retrained from their recorded config and data snapshot)."
        )
    op.drop_index("ix_candidate_estimates_player_id", table_name="candidate_estimates")
    op.drop_index("ix_candidate_estimates_version", table_name="candidate_estimates")
    op.drop_table("candidate_estimates")
    with op.batch_alter_table("model_versions") as batch:
        batch.drop_column("lineage")
        batch.drop_column("config")
        batch.drop_column("status")
        batch.alter_column("version", existing_type=sa.String(64), type_=sa.String(30), existing_nullable=False)
