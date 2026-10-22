from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class ClaudeUsageWindow(BaseModel):
    used_percent: float
    resets_at: datetime | None = None


class ClaudeUsageResponse(BaseModel):
    # "unavailable": no usage endpoint for the configured Claude proxy, so the
    # UI shows nothing. "error": the endpoint exists but the fetch failed.
    status: Literal["ok", "error", "unavailable"]
    provider: str | None = None
    session: ClaudeUsageWindow | None = None
    weekly: ClaudeUsageWindow | None = None
    error: str | None = None
    fetched_at: datetime
