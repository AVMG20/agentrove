import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.deps import get_automation_service
from app.models.db_models.automation import Automation
from app.services import automation as automation_module
from app.services.automation import AutomationService, github_webhook_url
from app.services.exceptions import GitHubException

from tests.conftest import LoginClient, UserFactory
from tests.helpers import create_authenticated_workspace
from tests.test_automation_github import (
    FakeGitHub,
    github_automation,
    make_event,
    set_github_token,
)
from tests.test_automations import FakeChatService, automation_payload

pytestmark = pytest.mark.anyio


class FakeHookGitHub(FakeGitHub):
    def __init__(self, fail: GitHubException | None = None) -> None:
        super().__init__([])
        self.fail = fail
        self.created: list[tuple[str, str, str, list[str]]] = []
        self.deleted: list[tuple[str, int]] = []

    async def create_repo_webhook(
        self, repo: str, url: str, secret: str, events: list[str]
    ) -> int:
        if self.fail:
            raise self.fail
        self.created.append((repo, url, secret, events))
        return 4242 + len(self.created)

    async def delete_repo_webhook(self, repo: str, hook_id: int) -> None:
        self.deleted.append((repo, hook_id))


@pytest.fixture(autouse=True)
def public_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "BASE_URL", "https://ai.example.com")
    monkeypatch.setattr(automation_module, "GITHUB_WEBHOOK_SETTLE_SECONDS", 0)


def sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def use_service(
    app: FastAPI, github: FakeGitHub, chats: FakeChatService
) -> AutomationService:
    service = AutomationService(
        chats,  # type: ignore[arg-type]
        github_service_factory=lambda _t: github,  # type: ignore[arg-type,return-value]
    )
    app.dependency_overrides[get_automation_service] = lambda: service
    return service


async def test_github_automation_registers_and_removes_a_repo_webhook(
    app: FastAPI,
    client: AsyncClient,
    db_session: AsyncSession,
    create_user: UserFactory,
    login: LoginClient,
) -> None:
    headers, user, workspace = await create_authenticated_workspace(
        db_session, create_user, login
    )
    await set_github_token(db_session, user.id)
    github = FakeHookGitHub()
    use_service(app, github, FakeChatService())

    created = await client.post(
        "/api/v1/automations",
        json=automation_payload(
            workspace.id,
            trigger_type="github",
            github_repo="acme/app",
            github_events=["pull_request_opened", "issue_comment_created"],
        ),
        headers=headers,
    )
    assert created.status_code == 201
    body = created.json()
    assert body["github_webhook_active"] is True
    assert body["github_webhook_error"] is None
    assert "github_webhook_secret" not in body
    repo, url, secret, events = github.created[0]
    assert repo == "acme/app"
    # The address the user reached the API on, not the BASE_URL setting.
    assert url == f"http://testserver/api/v1/automations/{body['id']}/github-webhook"
    assert len(secret) == 64
    assert events == ["issue_comment", "pull_request"]

    # Switching repos moves the hook.
    moved = await client.patch(
        f"/api/v1/automations/{body['id']}",
        json={"github_repo": "acme/other"},
        headers=headers,
    )
    assert moved.json()["github_webhook_active"] is True
    assert github.deleted == [("acme/app", 4243)]
    assert github.created[1][0] == "acme/other"

    deleted = await client.delete(f"/api/v1/automations/{body['id']}", headers=headers)
    assert deleted.status_code == 204
    assert github.deleted[-1] == ("acme/other", 4244)


def test_webhook_url_needs_a_public_address(monkeypatch: pytest.MonkeyPatch) -> None:
    automation_id = UUID(int=1)
    suffix = f"/api/v1/automations/{automation_id}/github-webhook"
    assert github_webhook_url(automation_id, "https://ai.avmg.nl/") == (
        f"https://ai.avmg.nl{suffix}"
    )
    # A local request address falls back to the configured public BASE_URL.
    assert github_webhook_url(automation_id, "http://localhost:3000/") == (
        f"https://ai.example.com{suffix}"
    )
    monkeypatch.setattr(get_settings(), "BASE_URL", "http://localhost:8080")
    assert github_webhook_url(automation_id, "http://127.0.0.1/") is None
    monkeypatch.setattr(get_settings(), "DESKTOP_MODE", True)
    assert github_webhook_url(automation_id, "https://ai.avmg.nl/") is None


async def test_webhook_setup_failure_keeps_polling(
    app: FastAPI,
    client: AsyncClient,
    db_session: AsyncSession,
    create_user: UserFactory,
    login: LoginClient,
) -> None:
    headers, user, workspace = await create_authenticated_workspace(
        db_session, create_user, login
    )
    await set_github_token(db_session, user.id)
    github = FakeHookGitHub(
        fail=GitHubException(
            "GitHub token can't manage webhooks on this repository", status_code=403
        )
    )
    use_service(app, github, FakeChatService())

    created = await client.post(
        "/api/v1/automations",
        json=automation_payload(
            workspace.id,
            trigger_type="github",
            github_repo="acme/app",
            github_events=["issue_opened"],
        ),
        headers=headers,
    )

    assert created.status_code == 201
    body = created.json()
    assert body["github_webhook_active"] is False
    assert body["github_webhook_error"] == (
        "GitHub token can't manage webhooks on this repository"
    )


async def test_signed_delivery_runs_the_check_and_bad_signatures_are_rejected(
    app: FastAPI,
    client: AsyncClient,
    db_session: AsyncSession,
    create_user: UserFactory,
    login: LoginClient,
) -> None:
    _headers, user, workspace = await create_authenticated_workspace(
        db_session, create_user, login
    )
    await set_github_token(db_session, user.id)
    automation = github_automation(
        user.id, workspace.id, datetime.now(timezone.utc) - timedelta(minutes=10)
    )
    automation.github_webhook_id = 1
    automation.github_webhook_secret = "s3cret"
    automation.next_run_at = datetime.now(timezone.utc) + timedelta(hours=1)
    db_session.add(automation)
    await db_session.commit()
    github = FakeHookGitHub()
    github.events = [make_event(1, 9)]
    chats = FakeChatService()
    use_service(app, github, chats)
    url = f"/api/v1/automations/{automation.id}/github-webhook"
    opened = json.dumps({"action": "opened"}).encode()

    forged = await client.post(
        url,
        content=opened,
        headers={"X-GitHub-Event": "issues", "X-Hub-Signature-256": "sha256=00"},
    )
    assert forged.status_code == 404

    edited = json.dumps({"action": "edited"}).encode()
    ignored = await client.post(
        url,
        content=edited,
        headers={
            "X-GitHub-Event": "issues",
            "X-Hub-Signature-256": sign("s3cret", edited),
        },
    )
    assert ignored.status_code == 202 and ignored.json() == {"queued": False}
    assert chats.created_chats == []

    delivered = await client.post(
        url,
        content=opened,
        headers={
            "X-GitHub-Event": "issues",
            "X-Hub-Signature-256": sign("s3cret", opened),
        },
    )
    assert delivered.status_code == 202 and delivered.json() == {"queued": True}
    assert [c.title for c in chats.created_chats] == ["Triage · Issue #9"]

    # A redelivery finds nothing new past the cursor.
    await client.post(
        url,
        content=opened,
        headers={
            "X-GitHub-Event": "issues",
            "X-Hub-Signature-256": sign("s3cret", opened),
        },
    )
    assert len(chats.created_chats) == 1


async def test_webhook_automation_polls_only_as_hourly_catch_up(
    db_session: AsyncSession,
    create_user: UserFactory,
    login: LoginClient,
) -> None:
    _headers, user, workspace = await create_authenticated_workspace(
        db_session, create_user, login
    )
    await set_github_token(db_session, user.id)
    automation = github_automation(
        user.id, workspace.id, datetime.now(timezone.utc) - timedelta(minutes=10)
    )
    automation.github_webhook_id = 1
    db_session.add(automation)
    await db_session.commit()
    service = AutomationService(
        FakeChatService(),  # type: ignore[arg-type]
        github_service_factory=lambda _t: FakeHookGitHub(),  # type: ignore[arg-type,return-value]
    )

    await service.run_due_automations()

    refreshed = await db_session.get(Automation, automation.id, populate_existing=True)
    assert refreshed is not None
    assert refreshed.next_run_at >= datetime.now(timezone.utc) + timedelta(minutes=59)
