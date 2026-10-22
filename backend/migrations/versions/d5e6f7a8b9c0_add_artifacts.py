# Revision ID: d5e6f7a8b9c0
# Revises: c3d4e5f6a7b8
# Create Date: 2026-10-22 00:00:00.000000

from typing import Sequence

from alembic import op
import sqlalchemy as sa
from app.db.migration_helpers import uuid_server_default, now_server_default
from app.db.types import GUID

# revision identifiers, used by Alembic.
revision: str = "d5e6f7a8b9c0"
down_revision: str | None = "c3d4e5f6a7b8"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "artifacts",
        sa.Column("id", GUID(), server_default=uuid_server_default(), nullable=False),
        sa.Column("user_id", GUID(), nullable=False),
        sa.Column("chat_id", GUID(), nullable=True),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("html", sa.Text(), nullable=False),
        sa.Column("is_public", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=now_server_default(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=now_server_default(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["chat_id"], ["chats.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_artifacts_user_id_updated_at",
        "artifacts",
        ["user_id", "updated_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("idx_artifacts_user_id_updated_at", table_name="artifacts")
    op.drop_table("artifacts")
