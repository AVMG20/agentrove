# Revision ID: c3d4e5f6a7b8
# Revises: a1c2e3f4b5d6
# Create Date: 2026-10-22 00:00:00.000000

from typing import Sequence

from alembic import op
import sqlalchemy as sa
from app.db.types import EncryptedString

# revision identifiers, used by Alembic.
revision: str = "c3d4e5f6a7b8"
down_revision: str | None = "a1c2e3f4b5d6"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "automations", sa.Column("github_webhook_id", sa.BigInteger(), nullable=True)
    )
    op.add_column(
        "automations",
        sa.Column("github_webhook_secret", EncryptedString(), nullable=True),
    )
    op.add_column(
        "automations",
        sa.Column("github_webhook_error", sa.String(length=255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("automations", "github_webhook_error")
    op.drop_column("automations", "github_webhook_secret")
    op.drop_column("automations", "github_webhook_id")
