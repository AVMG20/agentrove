from datetime import datetime, timedelta, timezone
from uuid import UUID

import httpx
import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_automation_service
from app.models.db_models.automation import Automation
from app.models.schemas.github import GitHubActivityEvent
from app.services.automation import MAX_GITHUB_EVENTS_PER_POLL, AutomationService
from app.services.github import GitHubService

from tests.conftest import LoginClient, UserFactory
from tests.helpers import create_authenticated_workspace, get_user_settings
from tests.test_automations import (
    TEST_MODEL_ID,
    AutomationServiceOverride,
    FakeChatService,
    automation_payload,
)


pytestmark = pytest.mark.anyio


def make_event(
    minutes_ago: int, number: int, author: str = "octo"
) -> GitHubActivityEvent:
    return GitHubActivityEvent(
        event="issue_opened",
        repo="acme/app",
        number=number,
        title=f"Issue {number}",
        url=f"https://github.com/acme/app/issues/{number}",
        author=author,
        body="Steps to reproduce",
        created_at=datetime.now(timezone.utc) - timedelta(minutes=minutes_ago),
    )


class FakeGitHub:
    def __init__(self, events: list[GitHubActivityEvent]) -> None:
        self.events = events
        self.calls: list[tuple[str, list[str], datetime, str | None]] = []

    async def get_authenticated_login(self) -> str:
        return "agentrove-bot"

    async def list_activity(
        self,
        repo: str,
        events: list[str],
        since: datetime,
        exclude_author: str | None = None,
    ) -> list[GitHubActivityEvent]:
        self.calls.append((repo, list(events), since, exclude_author))
        return [
            e
            for e in self.events
            if e.created_at > since and e.author != exclude_author
        ]


async def set_github_token(db: AsyncSession, user_id: UUID) -> None:
    settings = await get_user_settings(db, user_id)
    assert settings is not None
    settings.github_personal_access_token = "ghp_test"
    await db.commit()


def github_automation(
    user_id: UUID, workspace_id: UUID, cursor: datetime | None
) -> Automation:
    return Automation(
        user_id=user_id,
        workspace_id=workspace_id,
        name="Triage",
        prompt="Triage this",
        model_id=TEST_MODEL_ID,
        cron_expression="*/5 * * * *",
        timezone="UTC",
        next_run_at=datetime.now(timezone.utc) - timedelta(minutes=1),
        trigger_type="github",
        github_repo="acme/app",
        github_events=["issue_opened"],
        github_cursor=cursor,
    )


async def test_create_github_automation_validates_trigger_config(
    app: FastAPI,
    client: AsyncClient,
    db_session: AsyncSession,
    create_user: UserFactory,
    login: LoginClient,
) -> None:
    headers, _user, workspace = await create_authenticated_workspace(
        db_session, create_user, login
    )
    app.dependency_overrides[get_automation_service] = AutomationServiceOverride(
        FakeChatService()
    )

    missing_events = await client.post(
        "/api/v1/automations",
        json=automation_payload(
            workspace.id, trigger_type="github", github_repo="acme/app"
        ),
        headers=headers,
    )
    bad_repo = await client.post(
        "/api/v1/automations",
        json=automation_payload(
            workspace.id,
            trigger_type="github",
            github_repo="not a repo",
            github_events=["issue_opened"],
        ),
        headers=headers,
    )
    created = await client.post(
        "/api/v1/automations",
        json=automation_payload(
            workspace.id,
            trigger_type="github",
            github_repo="https://github.com/acme/app/",
            github_events=["issue_opened", "issue_opened", "pull_request_opened"],
        ),
        headers=headers,
    )

    assert missing_events.status_code == 422
    assert bad_repo.status_code == 422
    assert created.status_code == 201
    body = created.json()
    assert body["github_repo"] == "acme/app"
    assert body["github_events"] == ["issue_opened", "pull_request_opened"]

    cleared = await client.patch(
        f"/api/v1/automations/{body['id']}",
        json={"github_events": []},
        headers=headers,
    )
    assert cleared.status_code == 400


async def test_due_github_automation_fires_once_per_new_event_and_skips_self(
    db_session: AsyncSession,
    create_user: UserFactory,
    login: LoginClient,
) -> None:
    _headers, user, workspace = await create_authenticated_workspace(
        db_session, create_user, login
    )
    await set_github_token(db_session, user.id)
    cursor = datetime.now(timezone.utc) - timedelta(minutes=30)
    automation = github_automation(user.id, workspace.id, cursor)
    db_session.add(automation)
    await db_session.commit()
    github = FakeGitHub(
        [
            make_event(60, 1),  # before the cursor: already handled
            make_event(20, 2),
            make_event(10, 3, author="agentrove-bot"),  # our own activity
            make_event(5, 4),
        ]
    )
    chats = FakeChatService()
    service = AutomationService(chats, github_service_factory=lambda _t: github)  # type: ignore[arg-type,return-value]

    await service.run_due_automations()

    assert [c.title for c in chats.created_chats] == [
        "Triage · Issue #2",
        "Triage · Issue #4",
    ]
    prompt = chats.completions[0][0].prompt
    assert prompt.startswith("Triage this\n\n<github_event>")
    assert "url: https://github.com/acme/app/issues/2" in prompt
    assert github.calls[0][3] == "agentrove-bot"
    await db_session.refresh(automation)
    assert automation.github_cursor == github.events[3].created_at

    # Same events on the next tick: the cursor moved past them.
    await db_session.execute(
        Automation.__table__.update()
        .where(Automation.id == automation.id)
        .values(next_run_at=datetime.now(timezone.utc) - timedelta(minutes=1))
    )
    await db_session.commit()
    await service.run_due_automations()
    assert len(chats.created_chats) == 2


async def test_github_poll_caps_runs_and_starts_from_now_without_cursor(
    db_session: AsyncSession,
    create_user: UserFactory,
    login: LoginClient,
) -> None:
    _headers, user, workspace = await create_authenticated_workspace(
        db_session, create_user, login
    )
    await set_github_token(db_session, user.id)
    busy = github_automation(
        user.id, workspace.id, datetime.now(timezone.utc) - timedelta(days=1)
    )
    fresh = github_automation(user.id, workspace.id, None)
    db_session.add_all([busy, fresh])
    await db_session.commit()
    github = FakeGitHub([make_event(100 - i, i) for i in range(1, 15)])
    chats = FakeChatService()
    service = AutomationService(chats, github_service_factory=lambda _t: github)  # type: ignore[arg-type,return-value]

    await service.run_due_automations()

    assert len(chats.created_chats) == MAX_GITHUB_EVENTS_PER_POLL
    await db_session.refresh(fresh)
    assert fresh.github_cursor is not None


async def test_github_poll_without_token_does_nothing(
    db_session: AsyncSession,
    create_user: UserFactory,
    login: LoginClient,
) -> None:
    _headers, user, workspace = await create_authenticated_workspace(
        db_session, create_user, login
    )
    cursor = datetime.now(timezone.utc) - timedelta(minutes=30)
    db_session.add(github_automation(user.id, workspace.id, cursor))
    await db_session.commit()
    github = FakeGitHub([make_event(5, 1)])
    chats = FakeChatService()

    await AutomationService(
        chats,
        github_service_factory=lambda _t: github,  # type: ignore[arg-type,return-value]
    ).run_due_automations()

    assert chats.created_chats == []
    assert github.calls == []


async def test_run_now_replays_latest_github_event_without_moving_cursor(
    db_session: AsyncSession,
    create_user: UserFactory,
    login: LoginClient,
) -> None:
    _headers, user, workspace = await create_authenticated_workspace(
        db_session, create_user, login
    )
    await set_github_token(db_session, user.id)
    cursor = datetime.now(timezone.utc)
    automation = github_automation(user.id, workspace.id, cursor)
    db_session.add(automation)
    await db_session.commit()
    github = FakeGitHub([make_event(90, 7), make_event(30, 8)])
    chats = FakeChatService()
    service = AutomationService(chats, github_service_factory=lambda _t: github)  # type: ignore[arg-type,return-value]

    await service.run_automation(automation.id, user)

    assert chats.created_chats[0].title == "Triage · Issue #8"
    await db_session.refresh(automation)
    assert automation.github_cursor == cursor


async def test_list_activity_parses_issues_comments_and_releases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    since = datetime(2026, 10, 1, tzinfo=timezone.utc)

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/issues"):
            return httpx.Response(
                200,
                json=[
                    {
                        "number": 5,
                        "title": "Add dark mode",
                        "html_url": "https://github.com/acme/app/pull/5",
                        "user": {"login": "dev"},
                        "body": "PR body",
                        "created_at": "2026-10-02T10:00:00Z",
                        "pull_request": {},
                    },
                    {
                        "number": 4,
                        "title": "Old bug",
                        "html_url": "https://github.com/acme/app/issues/4",
                        "user": {"login": "dev"},
                        "body": None,
                        # Updated recently but created before `since`
                        "created_at": "2026-09-01T10:00:00Z",
                    },
                ],
            )
        if path.endswith("/issues/comments"):
            return httpx.Response(
                200,
                json=[
                    {
                        "html_url": "https://github.com/acme/app/issues/4#c1",
                        "issue_url": "https://api.github.com/repos/acme/app/issues/4",
                        "user": {"login": "me"},
                        "body": "ping",
                        "created_at": "2026-10-03T10:00:00Z",
                    }
                ],
            )
        if path.endswith("/releases"):
            return httpx.Response(
                200,
                json=[
                    {
                        "tag_name": "v1.0.0",
                        "name": "",
                        "draft": False,
                        "html_url": "https://github.com/acme/app/releases/v1.0.0",
                        "author": {"login": "dev"},
                        "body": "notes",
                        "created_at": "2026-09-30T10:00:00Z",
                        "published_at": "2026-10-04T10:00:00Z",
                    },
                    {"tag_name": "v2", "draft": True, "published_at": None},
                ],
            )
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        "app.services.github.httpx.AsyncClient",
        lambda **kw: real_client(transport=transport, **kw),
    )

    events = await GitHubService("t").list_activity(
        "acme/app",
        [
            "pull_request_opened",
            "issue_opened",
            "issue_comment_created",
            "release_published",
        ],
        since,
        exclude_author="me",
    )

    assert [(e.event, e.number, e.title) for e in events] == [
        ("pull_request_opened", 5, "Add dark mode"),
        ("release_published", None, "v1.0.0"),
    ]
