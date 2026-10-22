import re
from datetime import datetime
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from croniter import croniter
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.types import PermissionMode
from app.prompts.system_prompt import DEFAULT_PERSONA_NAME


AutomationTriggerType = Literal["schedule", "github"]
GitHubTriggerEvent = Literal[
    "pull_request_opened",
    "issue_opened",
    "issue_comment_created",
    "release_published",
]

_GITHUB_REPO_RE = re.compile(r"^[A-Za-z0-9-]+/[A-Za-z0-9._-]+$")


def validate_github_repo(value: str) -> str:
    value = value.strip().removeprefix("https://github.com/").rstrip("/")
    if not _GITHUB_REPO_RE.match(value):
        raise ValueError("GitHub repository must look like owner/name")
    return value


def validate_github_events(
    value: list[GitHubTriggerEvent],
) -> list[GitHubTriggerEvent]:
    # Order-preserving dedupe so the stored list reads like the user's picks.
    return list(dict.fromkeys(value))


def validate_trigger_config(
    trigger_type: str,
    github_repo: str | None,
    github_events: list[GitHubTriggerEvent] | None,
) -> None:
    if trigger_type == "github" and not (github_repo and github_events):
        raise ValueError("GitHub triggers need a repository and at least one event")


def validate_cron_expression(value: str) -> str:
    # Syntax-only: whether the expression still has a future occurrence is
    # timezone-dependent (calendar-constrained and year-bounded crons), so
    # that check lives in AutomationService._compute_next_run_validated,
    # where the automation's zone is known.
    if not croniter.is_valid(value):
        raise ValueError("Invalid cron expression")
    return value


def validate_timezone_name(value: str) -> str:
    try:
        ZoneInfo(value)
    except (KeyError, ValueError) as e:
        raise ValueError("Unknown timezone") from e
    return value


class AutomationBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    prompt: str = Field(..., min_length=1, max_length=100000)
    model_id: str = Field(..., min_length=1, max_length=255)
    cron_expression: str = Field(..., min_length=1, max_length=128)
    timezone: str = Field("UTC", min_length=1, max_length=64)
    permission_mode: PermissionMode = "bypassPermissions"
    thinking_mode: str | None = Field(None, max_length=50)
    worktree: bool = False
    selected_persona_name: str = Field(DEFAULT_PERSONA_NAME, max_length=100)
    enabled: bool = True
    # For "github", cron_expression is the poll interval.
    trigger_type: AutomationTriggerType = "schedule"
    github_repo: str | None = Field(None, max_length=255)
    github_events: list[GitHubTriggerEvent] | None = Field(None, max_length=10)

    @field_validator("cron_expression")
    @classmethod
    def check_cron_expression(cls, value: str) -> str:
        return validate_cron_expression(value)

    @field_validator("github_repo")
    @classmethod
    def check_github_repo(cls, value: str | None) -> str | None:
        return None if value is None else validate_github_repo(value)

    @field_validator("github_events")
    @classmethod
    def check_github_events(
        cls, value: list[GitHubTriggerEvent] | None
    ) -> list[GitHubTriggerEvent] | None:
        return None if value is None else validate_github_events(value)

    @model_validator(mode="after")
    def check_trigger(self) -> "AutomationBase":
        validate_trigger_config(self.trigger_type, self.github_repo, self.github_events)
        return self

    @field_validator("timezone")
    @classmethod
    def check_timezone(cls, value: str) -> str:
        return validate_timezone_name(value)


class AutomationCreate(AutomationBase):
    workspace_id: UUID


class AutomationUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)
    prompt: str | None = Field(None, min_length=1, max_length=100000)
    model_id: str | None = Field(None, min_length=1, max_length=255)
    workspace_id: UUID | None = None
    cron_expression: str | None = Field(None, min_length=1, max_length=128)
    timezone: str | None = Field(None, min_length=1, max_length=64)
    permission_mode: PermissionMode | None = None
    thinking_mode: str | None = Field(None, max_length=50)
    worktree: bool | None = None
    selected_persona_name: str | None = Field(None, min_length=1, max_length=100)
    enabled: bool | None = None
    # Combination with stored values is checked by the service after merging.
    trigger_type: AutomationTriggerType | None = None
    github_repo: str | None = Field(None, max_length=255)
    github_events: list[GitHubTriggerEvent] | None = Field(None, max_length=10)

    @field_validator(
        "name",
        "prompt",
        "model_id",
        "workspace_id",
        "cron_expression",
        "timezone",
        "permission_mode",
        "worktree",
        "selected_persona_name",
        "enabled",
        "trigger_type",
        mode="before",
    )
    @classmethod
    def reject_explicit_null(cls, value: object) -> object:
        # None here can only mean an explicit JSON null (validators don't run
        # for omitted fields): these columns are non-nullable, reject it.
        if value is None:
            raise ValueError("Field cannot be null")
        return value

    @field_validator("cron_expression")
    @classmethod
    def check_cron_expression(cls, value: str | None) -> str | None:
        return None if value is None else validate_cron_expression(value)

    @field_validator("timezone")
    @classmethod
    def check_timezone(cls, value: str | None) -> str | None:
        return None if value is None else validate_timezone_name(value)

    @field_validator("github_repo")
    @classmethod
    def check_github_repo(cls, value: str | None) -> str | None:
        return None if value is None else validate_github_repo(value)

    @field_validator("github_events")
    @classmethod
    def check_github_events(
        cls, value: list[GitHubTriggerEvent] | None
    ) -> list[GitHubTriggerEvent] | None:
        return None if value is None else validate_github_events(value)


class Automation(BaseModel):
    # Read model (not AutomationBase): skip write validators so stored rows
    # survive later rule drift — same pattern as Workspace.
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID
    workspace_id: UUID
    name: str
    prompt: str
    model_id: str
    cron_expression: str
    timezone: str
    permission_mode: str
    thinking_mode: str | None = None
    worktree: bool
    selected_persona_name: str
    enabled: bool
    next_run_at: datetime
    last_run_at: datetime | None = None
    trigger_type: str = "schedule"
    github_repo: str | None = None
    github_events: list[str] | None = None
    github_webhook_active: bool = False
    github_webhook_error: str | None = None
    created_at: datetime
    updated_at: datetime


class AutomationRunResponse(BaseModel):
    chat_id: UUID
