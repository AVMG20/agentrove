import asyncio
from typing import Any

import pytest
from acp.schema import (
    AgentMessageChunk,
    TextContentBlock,
    ToolCallProgress,
    ToolCallStart,
    UsageUpdate,
)

from app.models.types import AgentKind
from app.services import background_monitor as monitor_module
from app.services.acp.background import (
    background_launch,
    parse_probe_output,
    probe_command,
    stop_command,
)
from app.services.acp.client import AcpClientHandler
from app.services.background_monitor import BackgroundTaskMonitor

pytestmark = pytest.mark.anyio


def test_background_shell_is_read_from_structured_or_text_results() -> None:
    structured = background_launch(
        "Bash",
        "tool-1",
        {"command": "./gradlew build", "run_in_background": True},
        {"stdout": "", "stderr": "", "backgroundTaskId": "b1x"},
        100.0,
    )
    assert structured is not None
    assert (structured.id, structured.kind, structured.title) == (
        "b1x",
        "shell",
        "./gradlew build",
    )
    assert structured.started_at == 100.0 and structured.outlives_turn

    text = background_launch(
        "Bash",
        "tool-2",
        {"command": "npm run dev", "description": "Start the dev server"},
        "Command running in background with ID: bz9. Output is being written to: "
        "/tmp/x/tasks/bz9.output",
        0.0,
    )
    assert text is not None and (text.id, text.title) == (
        "bz9",
        "Start the dev server",
    )

    # A foreground command's output can say anything; only the marker counts.
    assert background_launch("Bash", "t", {"command": "ls"}, "file.txt", 0) is None
    # Ids end up in a shell script, so odd ones are refused.
    assert (
        background_launch("Bash", "t", {}, {"backgroundTaskId": "x; rm -rf /"}, 0)
        is None
    )


def test_async_agent_launch_is_tracked_until_the_turn_ends() -> None:
    launched = background_launch(
        "Agent",
        "tool-a",
        {"description": "Review the diff", "run_in_background": True},
        "Async agent launched successfully.\nagentId: a42 (internal ID)",
        5.0,
    )
    assert launched is not None
    assert (launched.id, launched.kind, launched.title) == (
        "a42",
        "agent",
        "Review the diff",
    )
    assert not launched.outlives_turn
    assert background_launch("Agent", "tool-a", {}, "Done: found 2 bugs", 0) is None


def test_probe_script_quotes_ids_and_parses_answers() -> None:
    command = probe_command(["b1", "b-2"])
    assert command.startswith("sh -c ") and command.endswith(" probe b1 b-2")
    assert stop_command("b1").endswith(" stop b1")
    assert parse_probe_output("1 b1\n0 b-2\nnoise\n") == {"b1": True, "b-2": False}


def _claude_tool(tool_id: str, name: str, **fields: Any) -> dict[str, Any]:
    return {
        "tool_call_id": tool_id,
        "field_meta": {"claudeCode": {"toolName": name}},
        **fields,
    }


async def test_handler_tracks_subagents_and_background_shells() -> None:
    handler = AcpClientHandler(agent_kind=AgentKind.CLAUDE)
    handler.prompt_active = True
    changes: list[list[str]] = []
    handler.on_background_tasks_changed = lambda: changes.append(
        sorted(handler.background_tasks)
    )

    await handler.session_update(
        "s",
        ToolCallStart(
            session_update="tool_call",
            title="Agent",
            raw_input={"description": "Explore the repo"},
            **_claude_tool("tool-agent", "Agent"),
        ),
    )
    assert handler.background_tasks["tool-agent"].title == "Explore the repo"

    await handler.session_update(
        "s",
        ToolCallStart(
            session_update="tool_call",
            title="./gradlew build",
            raw_input={"command": "./gradlew build", "run_in_background": True},
            **_claude_tool("tool-bash", "Bash"),
        ),
    )
    await handler.session_update(
        "s",
        ToolCallProgress(
            session_update="tool_call_update",
            status="completed",
            raw_output="Command running in background with ID: bsh1.",
            **_claude_tool("tool-bash", "Bash"),
        ),
    )
    await handler.session_update(
        "s",
        ToolCallProgress(
            session_update="tool_call_update",
            status="completed",
            raw_output="The repo has 3 packages.",
            **_claude_tool("tool-agent", "Agent"),
        ),
    )
    assert sorted(handler.background_tasks) == ["bsh1"]

    # The shell outlives the turn that started it.
    handler.finish(prompt_completed=True)
    assert sorted(handler.background_tasks) == ["bsh1"]
    assert changes[-1] == ["bsh1"]

    handler.background_tasks_ended(["bsh1"])
    assert handler.background_tasks == {}
    assert changes[-1] == []


async def test_unprompted_output_streams_until_the_autonomous_result() -> None:
    handler = AcpClientHandler(agent_kind=AgentKind.CLAUDE)
    woke: list[bool] = []
    handler.on_unprompted_output = lambda: woke.append(True)

    # Bookkeeping alone doesn't start a follow-up.
    await handler.session_update(
        "s", UsageUpdate(session_update="usage_update", used=1, size=10)
    )
    assert woke == [] and not handler.followup_active

    for text in ("Build finished", " — all green."):
        await handler.session_update(
            "s",
            AgentMessageChunk(
                session_update="agent_message_chunk",
                content=TextContentBlock(type="text", text=text),
            ),
        )
    assert woke == [True] and handler.followup_active

    await handler.session_update(
        "s",
        UsageUpdate(
            session_update="usage_update",
            used=2,
            size=10,
            field_meta={"_claude/origin": {"kind": "task-notification"}},
        ),
    )
    assert not handler.followup_active
    drained = []
    while not handler.event_queue.empty():
        drained.append(handler.event_queue.get_nowait())
    assert AcpClientHandler.is_sentinel(drained[-1])
    assert [e["type"] for e in drained[:-1]] == [
        "usage",
        "assistant_text",
        "assistant_text",
        "usage",
    ]


class FakeAcpSession:
    def __init__(self, alive: dict[str, bool]) -> None:
        self.handler = AcpClientHandler(agent_kind=AgentKind.CLAUDE)
        self.alive = alive
        self.stopped: list[str] = []

    def is_alive(self) -> bool:
        return True

    async def probe_background_shells(self, task_ids: list[str]) -> dict[str, bool]:
        return {task_id: self.alive.get(task_id, False) for task_id in task_ids}

    async def stop_background_shell(self, task_id: str) -> None:
        self.stopped.append(task_id)
        self.alive[task_id] = False


async def test_monitor_drops_exited_shells_and_stops_on_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(monitor_module, "PROBE_GRACE_SECONDS", 0)
    published: list[dict[str, Any]] = []

    async def fake_publish(self: BackgroundTaskMonitor) -> None:
        published.append({"tasks": self.snapshot()})

    monkeypatch.setattr(BackgroundTaskMonitor, "_publish", fake_publish)
    session = FakeAcpSession({"b1": True, "b2": False})

    async def start_followup(chat_id: str) -> None:
        return None

    monitor = BackgroundTaskMonitor(
        chat_id="chat-1",
        user_id="user-1",
        acp_session=session,  # type: ignore[arg-type]
        start_followup=start_followup,
    )
    for task_id in ("b1", "b2"):
        launched = background_launch(
            "Bash",
            f"tool-{task_id}",
            {"command": "sleep 99"},
            {"backgroundTaskId": task_id},
            0.0,
        )
        assert launched is not None
        session.handler._add_background_task(launched)

    await asyncio.sleep(0)
    assert published[-1]["tasks"][0]["id"] == "b1"

    await monitor.probe()
    assert [task.id for task in monitor.tasks] == ["b1"]

    assert await monitor.stop("missing") is False
    assert await monitor.stop("b1") is True
    assert session.stopped == ["b1"]
    assert monitor.tasks == []
    assert not monitor.has_running_shells()

    await asyncio.sleep(0)
    assert published[-1] == {"tasks": []}
    monitor.close()
    assert session.handler.on_background_tasks_changed is None
