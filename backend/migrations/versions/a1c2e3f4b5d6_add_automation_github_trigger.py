# Revision ID: a1c2e3f4b5d6
# Revises: fbca37896f4f
# Create Date: 2026-10-22 00:00:00.000000

from typing import Sequence

from alembic import op
import sqlalchemy as sa
from app.db.types import UTCDateTime

# revision identifiers, used by Alembic.
revision: str = "a1c2e3f4b5d6"
down_revision: str | None = "fbca37896f4f"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "automations",
        sa.Column(
            "trigger_type",
            sa.String(length=16),
            server_default="schedule",
            nullable=False,
        ),
    )
    op.add_column(
        "automations", sa.Column("github_repo", sa.String(length=255), nullable=True)
    )
    op.add_column("automations", sa.Column("github_events", sa.JSON(), nullable=True))
    op.add_column(
        "automations", sa.Column("github_cursor", UTCDateTime(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("automations", "github_cursor")
    op.drop_column("automations", "github_events")
    op.drop_column("automations", "github_repo")
    op.drop_column("automations", "trigger_type")
