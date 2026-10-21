"""rename opencode google-vertex-anthropic model ids to google-vertex

Revision ID: fbca37896f4f
Revises: e4f5a6b7c8d9
"""

from alembic import op
import sqlalchemy as sa

from app.db.types import GUID

revision = "fbca37896f4f"
down_revision = "e4f5a6b7c8d9"
branch_labels = None
depends_on = None

OLD_PREFIX = "opencode:google-vertex-anthropic/"
NEW_PREFIX = "opencode:google-vertex/"
COLUMNS = (
    ("messages", "model_id"),
    ("chats", "last_model_id"),
    ("automations", "model_id"),
    ("channel_members", "model_id"),
    ("user_settings", "title_model_id"),
)

user_settings = sa.table(
    "user_settings",
    sa.column("id", GUID),
    sa.column("stream_actions", sa.JSON),
)


def upgrade() -> None:
    bind = op.get_bind()
    for table, column in COLUMNS:
        bind.execute(
            sa.text(
                f"UPDATE {table} SET {column} = :new || substr({column}, :start) "
                f"WHERE {column} LIKE :pattern"
            ),
            {
                "new": NEW_PREFIX,
                "start": len(OLD_PREFIX) + 1,
                "pattern": OLD_PREFIX + "%",
            },
        )

    # JSON null decodes to None too, so SQL `IS NOT NULL` can't filter it.
    rows = bind.execute(
        sa.select(user_settings.c.id, user_settings.c.stream_actions)
    ).all()
    for row_id, actions in rows:
        if not actions or not any(
            a["model_id"].startswith(OLD_PREFIX) for a in actions
        ):
            continue
        renamed = [
            {
                **a,
                "model_id": NEW_PREFIX + a["model_id"].removeprefix(OLD_PREFIX)
                if a["model_id"].startswith(OLD_PREFIX)
                else a["model_id"],
            }
            for a in actions
        ]
        bind.execute(
            user_settings.update()
            .where(user_settings.c.id == row_id)
            .values(stream_actions=renamed)
        )


def downgrade() -> None:
    # OpenCode 2 rejects the old provider id, so there is nothing to restore.
    pass
