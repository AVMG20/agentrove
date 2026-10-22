import uuid
from uuid import UUID

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base
from app.db.migration_helpers import uuid_server_default
from app.db.types import GUID


class Artifact(Base):
    __tablename__ = "artifacts"

    id: Mapped[UUID] = mapped_column(
        GUID(),
        primary_key=True,
        default=uuid.uuid4,
        server_default=uuid_server_default(),
    )
    user_id: Mapped[UUID] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    # Chat the agent published it from; kept when that chat is deleted.
    chat_id: Mapped[UUID | None] = mapped_column(
        GUID(), ForeignKey("chats.id", ondelete="SET NULL"), nullable=True
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    html: Mapped[str] = mapped_column(Text, nullable=False)
    is_public: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    # Bumped on every title/html change so open viewers and chat cards can
    # tell an edit happened; earlier versions are not kept.
    version: Mapped[int] = mapped_column(
        Integer, default=1, server_default="1", nullable=False
    )

    __table_args__ = (
        Index("idx_artifacts_user_id_updated_at", "user_id", "updated_at"),
    )
