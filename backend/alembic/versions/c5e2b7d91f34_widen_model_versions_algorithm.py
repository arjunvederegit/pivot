"""R8.1: widen model_versions.algorithm so the model registry can be written on PostgreSQL

Revision ID: c5e2b7d91f34
Revises: a9d4c6e18b53
Create Date: 2026-10-18 20:00:00.000000

``model_versions.algorithm`` was declared ``VARCHAR(50)``. SQLite does not enforce a declared length,
so the project's real database holds labels of 51 characters (``weighted z-score index
(transparent, fixed weights)``, the production impact metric) and 97 (the pick-value curve). On
PostgreSQL — what docker compose and any deployment use — both inserts fail with
``StringDataRightTruncation`` and ``make train`` cannot register a model. Found by running the
suite against a real PostgreSQL server (R8.1); not caused by R8.

Widening is safe both ways round: an un-migrated SQLite database keeps working with the new code
(the length was never enforced there), and nothing existing is changed or dropped.
"""

import sqlalchemy as sa
from alembic import op

revision = "c5e2b7d91f34"
down_revision = "a9d4c6e18b53"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("model_versions") as batch:
        batch.alter_column(
            "algorithm", existing_type=sa.String(50), type_=sa.String(200), existing_nullable=False
        )


def downgrade() -> None:
    # On PostgreSQL this fails if a label longer than 50 characters is stored, which is the
    # honest outcome: narrowing would have to truncate or delete a model's description.
    with op.batch_alter_table("model_versions") as batch:
        batch.alter_column(
            "algorithm", existing_type=sa.String(200), type_=sa.String(50), existing_nullable=False
        )
