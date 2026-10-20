"""add channel message audience

Revision ID: e4f5a6b7c8d9
Revises: d7e8f9a0b1c2
"""

from alembic import op
import sqlalchemy as sa

revision = "e4f5a6b7c8d9"
down_revision = "d7e8f9a0b1c2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("channel_messages", sa.Column("audience", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("channel_messages", "audience")
