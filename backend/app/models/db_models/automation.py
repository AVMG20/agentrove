import uuid
from datetime import datetime
from uuid import UUID

from sqlalchemy import JSON, BigInteger, Boolean, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base
from app.db.migration_helpers import uuid_server_default
from app.db.types import GUID, EncryptedString, UTCDateTime


class Automation(Base):
    __tablename__ = "automations"

    id: Mapped[UUID] = mapped_column(
        GUID(),
        primary_key=True,
        default=uuid.uuid4,
        server_default=uuid_server_default(),
    )
    user_id: Mapped[UUID] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    workspace_id: Mapped[UUID] = mapped_column(
        GUID(), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    model_id: Mapped[str] = mapped_column(String(255), nullable=False)
    permission_mode: Mapped[str] = mapped_column(
        String(32),
        default="bypassPermissions",
        server_default="bypassPermissions",
        nullable=False,
    )
    thinking_mode: Mapped[str | None] = mapped_column(String(50), nullable=True)
    worktree: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    selected_persona_name: Mapped[str] = mapped_column(
        String(100), default="Default", server_default="Default", nullable=False
    )
    cron_expression: Mapped[str] = mapped_column(String(128), nullable=False)
    # IANA zone for cron so "daily at 9am" follows user local time/DST.
    timezone: Mapped[str] = mapped_column(
        String(64), default="UTC", server_default="UTC", nullable=False
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true", nullable=False
    )
    # Stored in UTC; the dispatch job fires rows where next_run_at <= now.
    next_run_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    last_run_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    # "schedule" fires the prompt on the cron; "github" uses the cron as the
    # poll interval and fires once per new matching event in github_repo.
    trigger_type: Mapped[str] = mapped_column(
        String(16), default="schedule", server_default="schedule", nullable=False
    )
    github_repo: Mapped[str | None] = mapped_column(String(255), nullable=True)
    github_events: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    # Events created at or before this instant were already handled (UTC).
    github_cursor: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    # Repo webhook that makes GitHub push events instantly; without one (token
    # can't manage hooks, no public URL) the cron poll is the only trigger.
    github_webhook_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    github_webhook_secret: Mapped[str | None] = mapped_column(
        EncryptedString, nullable=True
    )
    # Why the last webhook setup failed, shown next to the automation.
    github_webhook_error: Mapped[str | None] = mapped_column(String(255), nullable=True)

    @property
    def github_webhook_active(self) -> bool:
        return self.github_webhook_id is not None

    __table_args__ = (
        Index("idx_automations_user_id", "user_id"),
        Index("idx_automations_enabled_next_run_at", "enabled", "next_run_at"),
    )
