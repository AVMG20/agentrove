from __future__ import annotations

import re
import shlex
import time
from dataclasses import dataclass, field
from typing import Any, Literal

# Claude Code's text for a backgrounded Bash call and an async Agent launch.
# The structured toolResponse carries the same ids; the text is the fallback.
_SHELL_ID_RE = re.compile(r"Command running in background with ID: ([\w-]+)")
_AGENT_ID_RE = re.compile(r"agentId: ([\w-]+)")
_ASYNC_AGENT_MARKER = "Async agent launched"
# Ids reach a shell script, so only plain word characters pass.
_TASK_ID_RE = re.compile(r"[\w-]+")

SHELL_TOOL_NAMES = frozenset({"Bash"})
AGENT_TOOL_NAMES = frozenset({"Agent", "Task"})

MAX_TITLE_LENGTH = 200

# A shell runs as long as some process still writes its output file — Claude
# Code points the command's stdout/stderr at `<tmp>/tasks/<id>.output`, and
# every child inherits them. Linux reads /proc; macOS hosts have only lsof.
_HOLDERS_SH = r"""
holders() {
  if [ -d /proc/self/fd ]; then
    for p in /proc/[0-9]*; do
      for fd in 1 2; do
        case "$(readlink "$p/fd/$fd" 2>/dev/null)" in
          */tasks/"$1".output) echo "${p#/proc/}"; break ;;
        esac
      done
    done
  else
    lsof -d 1,2 -Fpn 2>/dev/null | awk -v suffix="/tasks/$1.output" '
      /^p/ { pid = substr($0, 2) }
      /^n/ && substr($0, length($0) - length(suffix) + 1) == suffix { print pid }
    ' | sort -u
  fi
}
"""

_PROBE_SH = (
    _HOLDERS_SH
    + r"""
for id in "$@"; do
  if [ -n "$(holders "$id")" ]; then echo "1 $id"; else echo "0 $id"; fi
done
"""
)

_STOP_SH = (
    _HOLDERS_SH
    + r"""
pids=$(holders "$1")
[ -z "$pids" ] && exit 0
kill -TERM $pids 2>/dev/null
sleep 2
pids=$(holders "$1")
[ -n "$pids" ] && kill -KILL $pids 2>/dev/null
exit 0
"""
)


@dataclass
class BackgroundTask:
    id: str
    kind: Literal["shell", "agent"]
    title: str
    tool_id: str
    # Wall-clock start of the tool call, so the UI timer survives reloads.
    started_at: float = field(default_factory=time.time)
    # Subagents run inside the agent's turn; only a shell outlives it.
    outlives_turn: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "title": self.title,
            "tool_id": self.tool_id,
            "started_at": self.started_at,
            "stoppable": self.kind == "shell",
        }


def _flatten_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return "\n".join(_flatten_text(v) for v in value.values())
    if isinstance(value, list):
        return "\n".join(_flatten_text(v) for v in value)
    return ""


def _title(tool_input: dict[str, Any] | None, *keys: str) -> str:
    for key in keys:
        value = (tool_input or {}).get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:MAX_TITLE_LENGTH]
    return ""


def background_launch(
    tool_name: str,
    tool_id: str,
    tool_input: dict[str, Any] | None,
    result: Any,
    started_at: float,
) -> BackgroundTask | None:
    # Reads a finished tool call for work that keeps running after it returned.
    if tool_name in SHELL_TOOL_NAMES:
        task_id = None
        if isinstance(result, dict):
            structured = result.get("backgroundTaskId")
            if isinstance(structured, str) and _TASK_ID_RE.fullmatch(structured):
                task_id = structured
        if task_id is None:
            match = _SHELL_ID_RE.search(_flatten_text(result))
            task_id = match.group(1) if match else None
        if task_id is None:
            return None
        return BackgroundTask(
            id=task_id,
            kind="shell",
            title=_title(tool_input, "description", "command") or "Shell command",
            tool_id=tool_id,
            started_at=started_at,
            outlives_turn=True,
        )
    if tool_name in AGENT_TOOL_NAMES:
        text = _flatten_text(result)
        launched = (
            isinstance(result, dict) and result.get("status") == "async_launched"
        ) or _ASYNC_AGENT_MARKER in text
        if not launched:
            return None
        agent_id = result.get("agentId") if isinstance(result, dict) else None
        if not isinstance(agent_id, str) or not _TASK_ID_RE.fullmatch(agent_id):
            match = _AGENT_ID_RE.search(text)
            agent_id = match.group(1) if match else tool_id
        return BackgroundTask(
            id=agent_id,
            kind="agent",
            title=_title(tool_input, "description", "subagent_type") or "Subagent",
            tool_id=tool_id,
            started_at=started_at,
        )
    return None


def foreground_agent(
    tool_name: str,
    tool_id: str,
    tool_input: dict[str, Any] | None,
    started_at: float,
) -> BackgroundTask | None:
    if tool_name not in AGENT_TOOL_NAMES:
        return None
    return BackgroundTask(
        id=tool_id,
        kind="agent",
        title=_title(tool_input, "description", "subagent_type") or "Subagent",
        tool_id=tool_id,
        started_at=started_at,
    )


def probe_command(task_ids: list[str]) -> str:
    return " ".join(
        ["sh", "-c", shlex.quote(_PROBE_SH), "probe", *map(shlex.quote, task_ids)]
    )


def stop_command(task_id: str) -> str:
    return " ".join(["sh", "-c", shlex.quote(_STOP_SH), "stop", shlex.quote(task_id)])


def parse_probe_output(output: str) -> dict[str, bool]:
    alive: dict[str, bool] = {}
    for line in output.splitlines():
        flag, _, task_id = line.strip().partition(" ")
        if flag in ("0", "1") and task_id:
            alive[task_id] = flag == "1"
    return alive
