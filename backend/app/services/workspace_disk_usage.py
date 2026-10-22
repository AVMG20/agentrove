from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

from app.models.db_models.workspace import Workspace
from app.services.sandbox_providers import SandboxProviderType
from app.services.sandbox_providers.base import SandboxProvider
from app.services.sandbox_providers.docker_provider import LocalDockerProvider

logger = logging.getLogger(__name__)

# Walking a repo with node_modules or a Gradle cache takes seconds, and sizes
# barely move minute to minute.
CACHE_TTL_SECONDS = 300.0
DU_TIMEOUT_SECONDS = 60.0
MAX_CONCURRENT_SCANS = 3


@dataclass(frozen=True)
class WorkspaceDiskUsage:
    files_bytes: int | None
    sandbox_bytes: int | None

    @property
    def total_bytes(self) -> int | None:
        parts = [p for p in (self.files_bytes, self.sandbox_bytes) if p is not None]
        return sum(parts) if parts else None


_cache: dict[str, tuple[float, WorkspaceDiskUsage]] = {}
_scan_slots = asyncio.Semaphore(MAX_CONCURRENT_SCANS)


async def _directory_bytes(path: str) -> int | None:
    process = await asyncio.create_subprocess_exec(
        "du",
        "-sk",
        path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        stdout, _ = await asyncio.wait_for(
            process.communicate(), timeout=DU_TIMEOUT_SECONDS
        )
    except TimeoutError:
        process.kill()
        await process.wait()
        return None
    # du still prints a total when some entries were unreadable.
    first = stdout.decode(errors="replace").split(maxsplit=1)
    return int(first[0]) * 1024 if first and first[0].isdigit() else None


async def _sandbox_bytes(workspace: Workspace) -> int | None:
    if workspace.sandbox_provider != SandboxProviderType.DOCKER.value:
        return None
    provider = SandboxProvider.create_provider(SandboxProviderType.DOCKER)
    assert isinstance(provider, LocalDockerProvider)
    try:
        return await provider.container_disk_usage(workspace.sandbox_id)
    finally:
        await provider.cleanup()


async def _measure(workspace: Workspace) -> WorkspaceDiskUsage:
    async with _scan_slots:
        files, sandbox = await asyncio.gather(
            _directory_bytes(workspace.workspace_path),
            _sandbox_bytes(workspace),
            return_exceptions=True,
        )
    if isinstance(files, BaseException):
        logger.warning("Sizing workspace %s failed: %s", workspace.id, files)
        files = None
    if isinstance(sandbox, BaseException):
        logger.warning("Sizing sandbox of %s failed: %s", workspace.id, sandbox)
        sandbox = None
    return WorkspaceDiskUsage(files_bytes=files, sandbox_bytes=sandbox)


async def workspace_disk_usage(
    workspaces: list[Workspace], *, refresh: bool = False
) -> dict[str, WorkspaceDiskUsage]:
    now = time.monotonic()

    async def one(workspace: Workspace) -> tuple[str, WorkspaceDiskUsage]:
        key = str(workspace.id)
        cached = _cache.get(key)
        if cached and not refresh and now - cached[0] < CACHE_TTL_SECONDS:
            return key, cached[1]
        usage = await _measure(workspace)
        _cache[key] = (time.monotonic(), usage)
        return key, usage

    return dict(await asyncio.gather(*(one(w) for w in workspaces)))
