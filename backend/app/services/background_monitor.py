from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from app.constants import REDIS_KEY_USER_CHATS_LIVE
from app.services.acp.background import BackgroundTask
from app.services.acp.session import AcpSession
from app.utils.cache import CacheError, cache_connection

logger = logging.getLogger(__name__)

PROBE_INTERVAL_SECONDS = 4.0
# A shell's output file appears a moment after its tool call returns.
PROBE_GRACE_SECONDS = 3.0


class BackgroundTaskMonitor:
    # Watches one chat's background shells and subagents: publishes the list
    # to the owner's live feed, notices when shells exit, and stops them.

    def __init__(
        self,
        *,
        chat_id: str,
        user_id: str,
        acp_session: AcpSession,
        start_followup: Callable[[str], Awaitable[None]],
    ) -> None:
        self.chat_id = chat_id
        self.user_id = user_id
        self._acp_session = acp_session
        self._start_followup = start_followup
        self._probe_task: asyncio.Task[None] | None = None
        self._publish_task: asyncio.Task[None] | None = None
        self._followup_task: asyncio.Task[None] | None = None
        self._dirty = False
        handler = acp_session.handler
        handler.on_background_tasks_changed = self._on_changed
        handler.on_unprompted_output = self._on_unprompted_output

    @property
    def tasks(self) -> list[BackgroundTask]:
        return sorted(
            self._acp_session.handler.background_tasks.values(),
            key=lambda task: task.started_at,
        )

    def has_running_shells(self) -> bool:
        return any(task.kind == "shell" for task in self.tasks)

    def snapshot(self) -> list[dict[str, Any]]:
        return [task.to_dict() for task in self.tasks]

    def _on_changed(self) -> None:
        # Coalesce bursts (a tool start + re-sent start) into one publish.
        self._dirty = True
        if self._publish_task is None or self._publish_task.done():
            self._publish_task = asyncio.create_task(self._publish())
        if self.has_running_shells() and (
            self._probe_task is None or self._probe_task.done()
        ):
            self._probe_task = asyncio.create_task(self._probe_loop())

    def _on_unprompted_output(self) -> None:
        if self._followup_task is not None and not self._followup_task.done():
            return
        self._followup_task = asyncio.create_task(self._run_followup())

    async def _run_followup(self) -> None:
        try:
            await self._start_followup(self.chat_id)
        except Exception:
            logger.error(
                "Failed to stream follow-up for chat %s", self.chat_id, exc_info=True
            )

    async def _publish(self) -> None:
        await asyncio.sleep(0)
        while self._dirty:
            self._dirty = False
            payload = {
                "kind": "background_tasks",
                "chat_id": self.chat_id,
                "tasks": self.snapshot(),
            }
            try:
                async with cache_connection() as cache:
                    await cache.publish(
                        REDIS_KEY_USER_CHATS_LIVE.format(user_id=self.user_id),
                        json.dumps(payload),
                    )
            except CacheError:
                logger.warning(
                    "Failed to publish background tasks for %s", self.chat_id
                )

    async def _probe_loop(self) -> None:
        while self.has_running_shells() and self._acp_session.is_alive():
            await asyncio.sleep(PROBE_INTERVAL_SECONDS)
            await self.probe()
        if not self._acp_session.is_alive():
            self._acp_session.handler.background_tasks_ended(
                [task.id for task in self.tasks]
            )

    async def probe(self) -> None:
        cutoff = time.time() - PROBE_GRACE_SECONDS
        shell_ids = [
            task.id
            for task in self.tasks
            if task.kind == "shell" and task.started_at <= cutoff
        ]
        if not shell_ids:
            return
        try:
            alive = await self._acp_session.probe_background_shells(shell_ids)
        except Exception as exc:
            # A failed probe proves nothing; keep the tasks until one succeeds.
            logger.debug("Background probe failed for %s: %s", self.chat_id, exc)
            return
        if not alive:
            return
        self._acp_session.handler.background_tasks_ended(
            [task_id for task_id in shell_ids if alive.get(task_id) is False]
        )

    async def stop(self, task_id: str) -> bool:
        task = self._acp_session.handler.background_tasks.get(task_id)
        if task is None or task.kind != "shell":
            return False
        await self._acp_session.stop_background_shell(task_id)
        await self.probe()
        return True

    def close(self) -> None:
        handler = self._acp_session.handler
        handler.on_background_tasks_changed = None
        handler.on_unprompted_output = None
        if self._probe_task is not None and not self._probe_task.done():
            self._probe_task.cancel()
        if self.tasks:
            # The agent process is going away and takes its shells with it.
            handler.background_tasks.clear()
            self._dirty = True
            if self._publish_task is None or self._publish_task.done():
                self._publish_task = asyncio.get_running_loop().create_task(
                    self._publish()
                )
