from urllib.parse import parse_qs, urlparse
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security import (
    create_artifact_agent_token,
    create_artifact_view_token,
    create_mcp_access_token,
)
from app.models.db_models.chat import Chat
from app.services.agent import AgentService
from app.services.sandbox_providers import SandboxProviderType

from tests.conftest import LoginClient, UserFactory
from tests.helpers import create_authenticated_workspace


pytestmark = pytest.mark.anyio

PAGE = "<!doctype html><title>Hi</title><script>document.title='ok'</script>"


async def _login_headers(
    create_user: UserFactory, login: LoginClient, email: str, username: str
) -> dict[str, str]:
    await create_user(email=email, username=username)
    tokens = await login(email=email)
    return {"Authorization": f"Bearer {tokens['access_token']}"}


async def _create(
    client: AsyncClient, headers: dict[str, str], **overrides: object
) -> dict[str, object]:
    payload: dict[str, object] = {"title": "Report", "html": PAGE}
    payload.update(overrides)
    response = await client.post("/api/v1/artifacts", json=payload, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


async def test_owner_crud_bumps_version_on_content_changes_only(
    client: AsyncClient, create_user: UserFactory, login: LoginClient
) -> None:
    headers = await _login_headers(create_user, login, "a@example.com", "alice")

    created = await _create(client, headers)
    assert created["is_public"] is False
    assert created["version"] == 1
    assert created["html"] == PAGE
    assert created["chat_id"] is None
    artifact_id = created["id"]

    response = await client.patch(
        f"/api/v1/artifacts/{artifact_id}",
        json={"html": "<p>v2</p>", "title": "Report v2"},
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json()["version"] == 2
    assert response.json()["title"] == "Report v2"

    # Same content again and visibility toggles are not new versions.
    response = await client.patch(
        f"/api/v1/artifacts/{artifact_id}",
        json={"html": "<p>v2</p>", "is_public": True},
        headers=headers,
    )
    assert response.json()["version"] == 2
    assert response.json()["is_public"] is True

    response = await client.get(f"/api/v1/artifacts/{artifact_id}", headers=headers)
    assert response.status_code == 200
    assert response.json()["html"] == "<p>v2</p>"

    response = await client.get("/api/v1/artifacts", headers=headers)
    assert [a["id"] for a in response.json()] == [artifact_id]
    assert "html" not in response.json()[0]

    response = await client.delete(f"/api/v1/artifacts/{artifact_id}", headers=headers)
    assert response.status_code == 204
    response = await client.get(f"/api/v1/artifacts/{artifact_id}", headers=headers)
    assert response.status_code == 404


async def test_update_rejects_explicit_null(
    client: AsyncClient, create_user: UserFactory, login: LoginClient
) -> None:
    headers = await _login_headers(create_user, login, "a@example.com", "alice")
    created = await _create(client, headers)

    response = await client.patch(
        f"/api/v1/artifacts/{created['id']}", json={"html": None}, headers=headers
    )

    assert response.status_code == 422


async def test_owner_endpoints_require_auth(client: AsyncClient) -> None:
    artifact_id = uuid4()
    assert (await client.get("/api/v1/artifacts")).status_code == 401
    assert (
        await client.post("/api/v1/artifacts", json={"title": "x", "html": "x"})
    ).status_code == 401
    assert (await client.get(f"/api/v1/artifacts/{artifact_id}")).status_code == 401
    assert (
        await client.patch(f"/api/v1/artifacts/{artifact_id}", json={"title": "x"})
    ).status_code == 401
    assert (await client.delete(f"/api/v1/artifacts/{artifact_id}")).status_code == 401


async def test_other_users_cannot_see_or_change_artifacts(
    client: AsyncClient, create_user: UserFactory, login: LoginClient
) -> None:
    owner = await _login_headers(create_user, login, "a@example.com", "alice")
    other = await _login_headers(create_user, login, "b@example.com", "bob")
    created = await _create(client, owner)
    path = f"/api/v1/artifacts/{created['id']}"

    assert (await client.get("/api/v1/artifacts", headers=other)).json() == []
    assert (await client.get(path, headers=other)).status_code == 404
    assert (await client.get(f"{path}/view", headers=other)).status_code == 404
    response = await client.patch(path, json={"is_public": True}, headers=other)
    assert response.status_code == 404
    assert (await client.delete(path, headers=other)).status_code == 404

    # Public makes it viewable, never editable, for others.
    await client.patch(path, json={"is_public": True}, headers=owner)
    response = await client.get(f"{path}/view", headers=other)
    assert response.status_code == 200
    assert response.json()["is_owner"] is False
    assert (await client.get(path, headers=other)).status_code == 404
    response = await client.patch(path, json={"html": "<p>x</p>"}, headers=other)
    assert response.status_code == 404


async def test_view_reports_visibility_and_content_path(
    client: AsyncClient, create_user: UserFactory, login: LoginClient
) -> None:
    headers = await _login_headers(create_user, login, "a@example.com", "alice")
    created = await _create(client, headers)
    path = f"/api/v1/artifacts/{created['id']}"

    # Private: hidden from anonymous viewers.
    assert (await client.get(f"{path}/view")).status_code == 404

    response = await client.get(f"{path}/view", headers=headers)
    assert response.status_code == 200
    view = response.json()
    assert view["is_owner"] is True
    assert "html" not in view
    content = urlparse(view["content_path"])
    assert content.path == f"/artifacts/{created['id']}/content"
    assert parse_qs(content.query)["view_token"]

    await client.patch(path, json={"is_public": True}, headers=headers)
    response = await client.get(f"{path}/view")
    assert response.status_code == 200
    assert response.json()["is_owner"] is False
    assert "view_token" not in response.json()["content_path"]


async def test_view_rejects_invalid_credentials_instead_of_anonymous(
    client: AsyncClient, create_user: UserFactory, login: LoginClient
) -> None:
    headers = await _login_headers(create_user, login, "a@example.com", "alice")
    created = await _create(client, headers)

    response = await client.get(
        f"/api/v1/artifacts/{created['id']}/view",
        headers={"Authorization": "Bearer expired-or-garbage"},
    )

    assert response.status_code == 401


async def test_content_is_sandboxed_and_gated_by_visibility(
    client: AsyncClient, create_user: UserFactory, login: LoginClient
) -> None:
    headers = await _login_headers(create_user, login, "a@example.com", "alice")
    created = await _create(client, headers)
    other = await _create(client, headers, title="Other")
    content_path = f"/api/v1/artifacts/{created['id']}/content"

    assert (await client.get(content_path)).status_code == 404
    wrong_token = create_artifact_view_token(other["id"])
    response = await client.get(content_path, params={"view_token": wrong_token})
    assert response.status_code == 404
    # A regular session token is not a view token.
    tokens = await login(email="a@example.com")
    response = await client.get(
        content_path, params={"view_token": tokens["access_token"]}
    )
    assert response.status_code == 404

    view = (
        await client.get(f"/api/v1/artifacts/{created['id']}/view", headers=headers)
    ).json()
    response = await client.get(f"/api/v1{view['content_path']}")
    assert response.status_code == 200
    assert response.text == PAGE
    assert response.headers["content-type"].startswith("text/html")
    policy = response.headers["content-security-policy"]
    assert policy.startswith("sandbox ")
    assert "allow-scripts" in policy
    assert "allow-same-origin" not in policy
    assert "x-frame-options" not in response.headers
    assert response.headers["referrer-policy"] == "no-referrer"
    assert "no-store" in response.headers["cache-control"]

    await client.patch(
        f"/api/v1/artifacts/{created['id']}", json={"is_public": True}, headers=headers
    )
    response = await client.get(content_path)
    assert response.status_code == 200
    assert "allow-same-origin" not in response.headers["content-security-policy"]

    # Everything else keeps refusing to be framed.
    response = await client.get(f"/api/v1/artifacts/{created['id']}/view")
    assert response.headers["x-frame-options"] == "DENY"


async def test_view_token_is_not_a_session(
    client: AsyncClient, create_user: UserFactory, login: LoginClient
) -> None:
    headers = await _login_headers(create_user, login, "a@example.com", "alice")
    created = await _create(client, headers)
    view_token = create_artifact_view_token(created["id"])

    response = await client.get("/api/v1/automations", params={"token": view_token})
    assert response.status_code == 401
    response = await client.get(
        "/api/v1/artifacts", headers={"Authorization": f"Bearer {view_token}"}
    )
    assert response.status_code == 401


async def test_agent_token_is_limited_to_publishing_artifacts(
    client: AsyncClient,
    db_session: AsyncSession,
    create_user: UserFactory,
    login: LoginClient,
) -> None:
    owner_headers, user, workspace = await create_authenticated_workspace(
        db_session, create_user, login
    )
    chat = Chat(title="Chat", user_id=user.id, workspace_id=workspace.id)
    db_session.add(chat)
    await db_session.commit()
    token = create_artifact_agent_token(str(user.id))
    agent = {"Authorization": f"Bearer {token}"}

    created = await _create(client, agent, chat_id=str(chat.id))
    assert created["chat_id"] == str(chat.id)
    path = f"/api/v1/artifacts/{created['id']}"

    response = await client.patch(path, json={"html": "<p>v2</p>"}, headers=agent)
    assert response.status_code == 200
    assert response.json()["version"] == 2
    response = await client.get("/api/v1/artifacts", headers=agent)
    assert [a["id"] for a in response.json()] == [created["id"]]
    assert (await client.get(path, headers=agent)).status_code == 200

    # Publishing to the world and deleting stay with the user.
    response = await client.patch(path, json={"is_public": True}, headers=agent)
    assert response.status_code == 403
    assert (await client.delete(path, headers=agent)).status_code == 403
    response = await client.get(path, headers=owner_headers)
    assert response.json()["is_public"] is False

    # The token opens nothing outside the artifacts API.
    assert (await client.get("/api/v1/workspaces", headers=agent)).status_code == 401
    response = await client.get("/api/v1/workspaces", params={"token": token})
    assert response.status_code == 401


async def test_full_mcp_token_manages_artifacts(
    client: AsyncClient, db_session: AsyncSession, create_user: UserFactory
) -> None:
    user = await create_user()
    headers = {"Authorization": f"Bearer {create_mcp_access_token(str(user.id))}"}

    created = await _create(client, headers)
    response = await client.patch(
        f"/api/v1/artifacts/{created['id']}", json={"is_public": True}, headers=headers
    )

    assert response.status_code == 200
    assert response.json()["is_public"] is True


async def test_create_rejects_someone_elses_chat(
    client: AsyncClient,
    db_session: AsyncSession,
    create_user: UserFactory,
    login: LoginClient,
) -> None:
    _headers, user, workspace = await create_authenticated_workspace(
        db_session, create_user, login
    )
    chat = Chat(title="Chat", user_id=user.id, workspace_id=workspace.id)
    db_session.add(chat)
    await db_session.commit()
    other = await _login_headers(create_user, login, "b@example.com", "bob")

    response = await client.post(
        "/api/v1/artifacts",
        json={"title": "x", "html": "x", "chat_id": str(chat.id)},
        headers=other,
    )

    assert response.status_code == 404


async def test_deleting_the_chat_keeps_its_artifacts(
    client: AsyncClient,
    db_session: AsyncSession,
    create_user: UserFactory,
    login: LoginClient,
) -> None:
    headers, user, workspace = await create_authenticated_workspace(
        db_session, create_user, login
    )
    chat = Chat(title="Chat", user_id=user.id, workspace_id=workspace.id)
    db_session.add(chat)
    await db_session.commit()
    created = await _create(client, headers, chat_id=str(chat.id))

    await db_session.delete(chat)
    await db_session.commit()

    response = await client.get(f"/api/v1/artifacts/{created['id']}", headers=headers)
    assert response.status_code == 200
    assert response.json()["chat_id"] is None


def _agentrove_env(
    sandbox_provider: SandboxProviderType, *, agentrove_mcp: bool = True
) -> dict[str, str] | None:
    servers = AgentService._build_mcp_server_configs(
        "chat-1",
        str(uuid4()),
        sandbox_provider,
        agentrove_mcp=agentrove_mcp,
        current_chat_id="chat-1",
    )
    agentrove = [s for s in servers if s["name"] == "agentrove"]
    return agentrove[0]["env"] if agentrove else None


def test_artifact_tools_reach_host_agents_without_the_full_mcp_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "AGENTROVE_MCP_ENABLED", False)
    monkeypatch.setattr(settings, "AGENTROVE_ARTIFACTS_ENABLED", True)
    monkeypatch.setattr(settings, "FRONTEND_URL", "https://ai.example.com")

    env = _agentrove_env(SandboxProviderType.HOST)
    assert env is not None
    assert env["AGENTROVE_MCP_TOOLSET"] == "artifacts"
    assert env["AGENTROVE_APP_URL"] == "https://ai.example.com"
    assert env["AGENTROVE_CURRENT_CHAT_ID"] == "chat-1"
    # Docker sandboxes don't ship the server; restricted channel members
    # don't get AgentRove tools at all.
    assert _agentrove_env(SandboxProviderType.DOCKER) is None
    assert _agentrove_env(SandboxProviderType.HOST, agentrove_mcp=False) is None

    monkeypatch.setattr(settings, "AGENTROVE_ARTIFACTS_ENABLED", False)
    assert _agentrove_env(SandboxProviderType.HOST) is None

    monkeypatch.setattr(settings, "AGENTROVE_MCP_ENABLED", True)
    env = _agentrove_env(SandboxProviderType.DOCKER)
    assert env is not None
    assert "AGENTROVE_MCP_TOOLSET" not in env
