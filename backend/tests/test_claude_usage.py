from datetime import datetime, timezone

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.endpoints import usage as usage_endpoint
from app.core.config import get_settings
from app.services.claude_usage import (
    ClaudeUsageService,
    UsageSource,
    parse_usage_body,
    resolve_usage_source,
)

from tests.conftest import LoginClient, UserFactory
from tests.helpers import get_user_settings

pytestmark = pytest.mark.anyio

USAGE_URL = "https://claude-admin.bitsec.dev/api/usage"
USAGE_BODY = {
    "five_hour": {"utilization": 51.0, "resets_at": "2026-10-22T15:00:00Z"},
    "seven_day": {"utilization": 91.4, "resets_at": 1792000000},
}


@pytest.fixture(autouse=True)
def clear_global_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(get_settings(), "AGENT_ENV", "")


def env_vars(base_url: str, token: str = "sk-user") -> list[dict[str, str]]:
    return [
        {"key": "ANTHROPIC_BASE_URL", "value": base_url},
        {"key": "ANTHROPIC_AUTH_TOKEN", "value": token},
    ]


def test_resolve_usage_source_only_for_known_proxy_hosts() -> None:
    settings = get_settings()

    source = resolve_usage_source(settings, env_vars("https://claude.bitsec.dev/"))
    assert source == UsageSource(
        provider="claude.bitsec.dev", url=USAGE_URL, token="sk-user"
    )
    # Unknown hosts never make the server fetch anything.
    assert resolve_usage_source(settings, env_vars("https://evil.example")) is None
    # No token → nothing to authenticate with.
    assert resolve_usage_source(settings, env_vars(USAGE_URL, token="")) is None
    assert resolve_usage_source(settings, None) is None


def test_resolve_usage_source_falls_back_to_deployment_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = get_settings()
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://claude.bitsec.dev")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "sk-deploy")

    assert resolve_usage_source(settings, None) == UsageSource(
        provider="claude.bitsec.dev", url=USAGE_URL, token="sk-deploy"
    )
    # The user's own token wins over the deployment's.
    user_source = resolve_usage_source(
        settings, [{"key": "ANTHROPIC_AUTH_TOKEN", "value": "sk-mine"}]
    )
    assert user_source is not None and user_source.token == "sk-mine"


def test_parse_usage_body_reads_both_windows() -> None:
    result = parse_usage_body("claude.bitsec.dev", USAGE_BODY)

    assert result.status == "ok"
    assert result.session is not None and result.session.used_percent == 51.0
    assert result.session.resets_at == datetime(2026, 10, 22, 15, tzinfo=timezone.utc)
    assert result.weekly is not None and result.weekly.used_percent == 91.4
    assert result.weekly.resets_at == datetime.fromtimestamp(
        1792000000, tz=timezone.utc
    )


def test_parse_usage_body_rejects_unexpected_shapes() -> None:
    assert parse_usage_body("p", []).status == "error"
    assert parse_usage_body("p", {"five_hour": {"resets_at": 1}}).status == "error"
    clamped = parse_usage_body("p", {"seven_day": {"utilization": 140}})
    assert clamped.status == "ok" and clamped.session is None
    assert clamped.weekly is not None and clamped.weekly.used_percent == 100.0
    # Out-of-range reset times are dropped instead of failing the request.
    for bad_reset in (1e20, "1e20", "Infinity", float("inf"), float("nan"), "soon"):
        result = parse_usage_body(
            "p", {"five_hour": {"utilization": 5, "resets_at": bad_reset}}
        )
        assert result.session is not None and result.session.resets_at is None
    assert (
        parse_usage_body("p", {"five_hour": {"utilization": float("nan")}}).status
        == "error"
    )


async def test_usage_service_caches_and_reports_errors() -> None:
    calls: list[httpx.Request] = []
    status = {"code": 200}

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(status["code"], json=USAGE_BODY)

    service = ClaudeUsageService(transport=httpx.MockTransport(handler))
    source = UsageSource(provider="claude.bitsec.dev", url=USAGE_URL, token="sk-a")

    first = await service.get_usage("u1", source)
    second = await service.get_usage("u1", source)
    # A manual refresh right after a fetch is throttled too.
    third = await service.get_usage("u1", source, refresh=True)

    assert first.status == "ok"
    assert second is first and third is first
    assert len(calls) == 1
    assert calls[0].headers["Authorization"] == "Bearer sk-a"

    status["code"] = 401
    other_token = UsageSource(provider=source.provider, url=USAGE_URL, token="sk-b")
    rejected = await service.get_usage("u1", other_token)
    assert rejected.status == "error"
    assert rejected.error == "Usage endpoint rejected the token"
    assert len(calls) == 2

    unavailable = await service.get_usage("u1", None)
    assert unavailable.status == "unavailable"


async def test_usage_endpoint_uses_the_users_env_vars(
    monkeypatch: pytest.MonkeyPatch,
    client: AsyncClient,
    db_session: AsyncSession,
    create_user: UserFactory,
    login: LoginClient,
) -> None:
    user = await create_user()
    tokens = await login()
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    monkeypatch.setattr(
        usage_endpoint,
        "claude_usage_service",
        ClaudeUsageService(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json=USAGE_BODY)
            )
        ),
    )

    before = await client.get("/api/v1/usage/claude", headers=headers)
    assert before.status_code == 200
    assert before.json()["status"] == "unavailable"

    user_settings = await get_user_settings(db_session, user.id)
    assert user_settings is not None
    user_settings.custom_env_vars = env_vars("https://claude.bitsec.dev")
    await db_session.commit()

    after = await client.get("/api/v1/usage/claude", headers=headers)
    body = after.json()
    assert body["status"] == "ok"
    assert body["provider"] == "claude.bitsec.dev"
    assert body["session"]["used_percent"] == 51.0
    # The raw token is never echoed back.
    assert "sk-user" not in after.text
