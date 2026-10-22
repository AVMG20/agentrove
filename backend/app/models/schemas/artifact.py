from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Single-file pages with inline assets; generous, but bounded so one agent
# call can't bloat the database.
MAX_ARTIFACT_HTML_LENGTH = 2_000_000


class ArtifactCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    html: str = Field(..., min_length=1, max_length=MAX_ARTIFACT_HTML_LENGTH)
    chat_id: UUID | None = None


class ArtifactUpdate(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=255)
    html: str | None = Field(None, min_length=1, max_length=MAX_ARTIFACT_HTML_LENGTH)
    is_public: bool | None = None

    @field_validator("title", "html", "is_public", mode="before")
    @classmethod
    def reject_explicit_null(cls, value: object) -> object:
        # None here can only mean an explicit JSON null (validators don't run
        # for omitted fields): these columns are non-nullable, reject it.
        if value is None:
            raise ValueError("Field cannot be null")
        return value


class Artifact(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    chat_id: UUID | None = None
    title: str
    is_public: bool
    version: int
    created_at: datetime
    updated_at: datetime


class ArtifactDetail(Artifact):
    html: str


class ArtifactView(Artifact):
    # What the viewer page needs: whether to show owner controls, and where to
    # point its sandboxed iframe (relative to the API base, carrying a
    # short-lived view token when the artifact is private).
    is_owner: bool
    content_path: str
