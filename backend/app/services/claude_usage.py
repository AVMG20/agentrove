from __future__ import annotations

import asyncio
import hashlib
import math
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import httpx

from app.core.config import Settings, agent_global_env
from app.models.schemas.usage import ClaudeUsageResponse, ClaudeUsageWindow
from app.models.types import CustomEnvVarDict

USAGE_TIMEOUT_SECONDS = 10.0
# Gateways rate-limit their usage endpoint; every open tab polls, so share one
# fetch per user for a while. A manual refresh may cut it short, not to zero.
CACHE_TTL_SECONDS = 60.0
MIN_REFRESH_SECONDS = 10.0


@dataclass(frozen=True)
class UsageSource:
    provider: str
    url: str
    token: str


def parse_usage_endpoints(raw: str) -> dict[str, str]:
    endpoints: dict[str, str] = {}
    for pair in raw.split(","):
        host, sep, url = pair.partition("=")
        host, url = host.strip().lower(), url.strip()
        if sep and host and url.startswith("https://"):
            endpoints[host] = url
    return endpoints


def resolve_usage_source(
    settings: Settings, custom_env_vars: list[CustomEnvVarDict] | None
) -> UsageSource | None:
    # Same precedence the agent sees: deployment env, then the user's own vars.
    env = agent_global_env(settings)
    for env_var in custom_env_vars or []:
        env[env_var["key"]] = env_var["value"]
    host = (urlparse(env.get("ANTHROPIC_BASE_URL", "").strip()).hostname or "").lower()
    url = parse_usage_endpoints(settings.CLAUDE_USAGE_ENDPOINTS).get(host)
    token = (
        env.get("ANTHROPIC_AUTH_TOKEN", "").strip()
        or env.get("ANTHROPIC_API_KEY", "").strip()
    )
    if not url or not token:
        return None
    return UsageSource(provider=host, url=url, token=token)


def _parse_reset(value: Any) -> datetime | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        if not math.isfinite(value) or value <= 0:
            return None
        # Seconds vs milliseconds epoch.
        seconds = value / 1000 if value > 10_000_000_000 else value
        try:
            return datetime.fromtimestamp(seconds, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str) and value.strip():
        text = value.strip()
        try:
            return _parse_reset(float(text))
        except ValueError:
            pass
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return None


def _parse_window(raw: Any) -> ClaudeUsageWindow | None:
    if not isinstance(raw, dict):
        return None
    used: Any = None
    for key in ("utilization", "used_percentage", "usedPercent"):
        value = raw.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            used = float(value)
            break
    if used is None or not math.isfinite(used):
        return None
    return ClaudeUsageWindow(
        used_percent=min(100.0, max(0.0, used)),
        resets_at=_parse_reset(raw.get("resets_at", raw.get("resetsAt"))),
    )


def parse_usage_body(provider: str, body: Any) -> ClaudeUsageResponse:
    # Same layout as Anthropic's OAuth usage: five_hour / seven_day windows.
    now = datetime.now(timezone.utc)
    if not isinstance(body, dict):
        return ClaudeUsageResponse(
            status="error",
            provider=provider,
            error="Usage response was not a JSON object",
            fetched_at=now,
        )
    session = _parse_window(body.get("five_hour"))
    weekly = _parse_window(body.get("seven_day"))
    if session is None and weekly is None:
        return ClaudeUsageResponse(
            status="error",
            provider=provider,
            error="Usage response had no 5-hour or weekly window",
            fetched_at=now,
        )
    return ClaudeUsageResponse(
        status="ok", provider=provider, session=session, weekly=weekly, fetched_at=now
    )


class ClaudeUsageService:
    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._transport = transport
        self._cache: dict[str, tuple[float, ClaudeUsageResponse]] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    @staticmethod
    def _cache_key(user_id: str, source: UsageSource) -> str:
        # Token hashed so a changed token never reuses the old account's numbers.
        digest = hashlib.sha256(source.token.encode()).hexdigest()[:16]
        return f"{user_id}:{source.url}:{digest}"

    async def get_usage(
        self, user_id: str, source: UsageSource | None, refresh: bool = False
    ) -> ClaudeUsageResponse:
        if source is None:
            return ClaudeUsageResponse(
                status="unavailable", fetched_at=datetime.now(timezone.utc)
            )
        key = self._cache_key(user_id, source)
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            cached = self._cache.get(key)
            if cached is not None:
                age = time.monotonic() - cached[0]
                limit = MIN_REFRESH_SECONDS if refresh else CACHE_TTL_SECONDS
                if age < limit:
                    return cached[1]
            result = await self._fetch(source)
            self._prune()
            self._cache[key] = (time.monotonic(), result)
            return result

    def _prune(self) -> None:
        # Rotated tokens leave dead keys behind; drop anything past its TTL.
        cutoff = time.monotonic() - CACHE_TTL_SECONDS
        for key, (fetched, _result) in list(self._cache.items()):
            lock = self._locks.get(key)
            if fetched < cutoff and not (lock and lock.locked()):
                self._cache.pop(key, None)
                self._locks.pop(key, None)

    async def _fetch(self, source: UsageSource) -> ClaudeUsageResponse:
        try:
            async with httpx.AsyncClient(
                timeout=USAGE_TIMEOUT_SECONDS,
                transport=self._transport,
                follow_redirects=False,
            ) as client:
                response = await client.get(
                    source.url,
                    headers={
                        "Authorization": f"Bearer {source.token}",
                        "Accept": "application/json",
                    },
                )
        except httpx.HTTPError:
            return self._error(source, "Usage endpoint unreachable")
        if response.status_code in (401, 403):
            return self._error(source, "Usage endpoint rejected the token")
        if not response.is_success:
            return self._error(
                source, f"Usage endpoint returned HTTP {response.status_code}"
            )
        try:
            return parse_usage_body(source.provider, response.json())
        except ValueError:
            return self._error(source, "Usage response was not valid JSON")

    @staticmethod
    def _error(source: UsageSource, message: str) -> ClaudeUsageResponse:
        return ClaudeUsageResponse(
            status="error",
            provider=source.provider,
            error=message,
            fetched_at=datetime.now(timezone.utc),
        )


claude_usage_service = ClaudeUsageService()
