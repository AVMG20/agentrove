from __future__ import annotations

import logging
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import anyio
from anyio.abc import TaskStatus
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.streamable_http import StreamableHTTPServerTransport
from mcp.server.transport_security import TransportSecuritySettings
from starlette.responses import JSONResponse
from starlette.types import Receive, Scope, Send

from app.constants import ARTIFACTS_MCP_CHAT_HEADER
from app.core.config import get_settings
from app.core.security import ARTIFACT_AGENT_AUDIENCE, decode_scoped_token
from app.db.session import SessionLocal
from app.models.db_models.artifact import Artifact
from app.models.db_models.user import User
from app.models.schemas.artifact import ArtifactCreate, ArtifactUpdate
from app.services.artifact import ArtifactService
from app.services.exceptions import ArtifactException, ChatException

# The artifact tools over streamable HTTP, for agents in Docker sandboxes: the
# sandbox image ships neither mcp-server/ nor a Python with the mcp package,
# but it can reach the backend's public URL. Tool texts mirror mcp-server/.

logger = logging.getLogger(__name__)
settings = get_settings()

# The chat the session runs in; new artifacts link back to it.
CHAT_ID_HEADER = ARTIFACTS_MCP_CHAT_HEADER.lower()


@dataclass(frozen=True)
class _Caller:
    user_id: UUID
    chat_id: UUID | None


# Set per request before the server task starts, which inherits it.
_caller: ContextVar[_Caller] = ContextVar("artifacts_mcp_caller")

artifacts_mcp = FastMCP("agentrove")
_service = ArtifactService(session_factory=SessionLocal)


def _summary(artifact: Artifact) -> dict[str, Any]:
    return {
        "id": str(artifact.id),
        "title": artifact.title,
        "url": f"{settings.FRONTEND_URL.rstrip('/')}/artifacts/{artifact.id}",
        "is_public": artifact.is_public,
        "version": artifact.version,
        "updated_at": artifact.updated_at.isoformat(),
    }


def _artifact_id(value: str) -> UUID:
    try:
        return UUID(value)
    except ValueError as exc:
        raise ToolError(f"Not an artifact id: {value}") from exc


@artifacts_mcp.tool()
async def create_artifact(title: str, html: str) -> dict[str, Any]:
    """Publish a self-contained HTML page as an artifact with its own shareable link.

    Use an artifact when the user wants something to open, keep or share as a page:
    a report, dashboard, chart, prototype, slide deck, game or document. Prefer it
    over writing an .html file into the workspace for anything meant to be viewed.

    `html` is a complete single-file document (<!doctype html>...) with inline <style>
    and <script>. Scripts, stylesheets and fonts from public CDNs (cdn.jsdelivr.net,
    unpkg.com, cdnjs.cloudflare.com, Google Fonts) and fetch() to public CORS APIs
    work. The page runs sandboxed with an opaque origin: cookies, localStorage,
    sessionStorage and AgentRove's API are unavailable, so keep state in memory. Make
    it responsive and readable on its own; it is shown full-screen.

    Artifacts start private: only the user, signed in, can open the link; they can
    make it public from the page. Returns the artifact's id, url, version and
    visibility. Always give the user the url (as a markdown link) in your reply. To
    revise it later, call update_artifact with its id instead of creating a new one.
    """
    caller = _caller.get()
    try:
        artifact = await _service.create_artifact(
            caller.user_id,
            ArtifactCreate(title=title, html=html, chat_id=caller.chat_id),
        )
    except ChatException:
        # A chat id the user doesn't own: publish unlinked rather than fail.
        artifact = await _service.create_artifact(
            caller.user_id, ArtifactCreate(title=title, html=html)
        )
    return {"artifact": _summary(artifact)}


@artifacts_mcp.tool()
async def update_artifact(
    artifact_id: str, html: str, title: str | None = None
) -> dict[str, Any]:
    """Replace an existing artifact's HTML, and optionally its title.

    `html` is the complete new document, not a diff (read the current one with
    get_artifact first when making targeted edits). The link stays the same and the
    version increments; open viewers pick up the new version on reload. Visibility is
    left unchanged. Returns the updated artifact's id, url, version and visibility;
    share the url with the user again.
    """
    changes = ArtifactUpdate(html=html, **({"title": title} if title else {}))
    try:
        artifact = await _service.update_artifact(
            _artifact_id(artifact_id), _caller.get().user_id, changes
        )
    except ArtifactException as exc:
        raise ToolError(str(exc)) from exc
    return {"artifact": _summary(artifact)}


@artifacts_mcp.tool()
async def list_artifacts() -> dict[str, Any]:
    """List the user's artifacts, most recently updated first.

    Returns each artifact's id, title, url, is_public, version and updated_at. Use an
    id with get_artifact or update_artifact.
    """
    artifacts = await _service.list_artifacts(_caller.get().user_id)
    return {"artifacts": [_summary(artifact) for artifact in artifacts]}


@artifacts_mcp.tool()
async def get_artifact(artifact_id: str) -> dict[str, Any]:
    """Get an artifact's metadata and its full current HTML.

    Use it to read the page before revising it with update_artifact. Returns id,
    title, url, is_public, version, updated_at and html.
    """
    try:
        artifact = await _service.get_artifact(
            _artifact_id(artifact_id), _caller.get().user_id
        )
    except ArtifactException as exc:
        raise ToolError(str(exc)) from exc
    return {"artifact": {**_summary(artifact), "html": artifact.html}}


async def _authenticate(scope: Scope) -> _Caller | None:
    headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
    scheme, _, token = headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None
    subject = decode_scoped_token(token, ARTIFACT_AGENT_AUDIENCE)
    if subject is None:
        return None
    async with SessionLocal() as db:
        user = await db.get(User, UUID(subject))
    if user is None or not user.is_active:
        return None
    try:
        chat_id = UUID(headers[CHAT_ID_HEADER]) if CHAT_ID_HEADER in headers else None
    except ValueError:
        chat_id = None
    return _Caller(user_id=user.id, chat_id=chat_id)


async def artifacts_mcp_app(scope: Scope, receive: Receive, send: Send) -> None:
    # Stateless streamable HTTP, one server run per request — the SDK's session
    # manager does the same but needs a lifespan-owned task group.
    if scope["type"] != "http":
        return
    caller = await _authenticate(scope)
    if caller is None:
        response = JSONResponse(
            {"detail": "Could not validate credentials"},
            status_code=401,
            headers={"WWW-Authenticate": "Bearer"},
        )
        await response(scope, receive, send)
        return
    _caller.set(caller)

    transport = StreamableHTTPServerTransport(
        mcp_session_id=None,
        is_json_response_enabled=True,
        event_store=None,
        # Reached through the public hostname; the bearer token is the guard.
        security_settings=TransportSecuritySettings(
            enable_dns_rebinding_protection=False
        ),
    )
    server = artifacts_mcp._mcp_server

    async def run_server(*, task_status: TaskStatus[None]) -> None:
        async with transport.connect() as (read_stream, write_stream):
            task_status.started()
            try:
                await server.run(
                    read_stream,
                    write_stream,
                    server.create_initialization_options(),
                    stateless=True,
                )
            except Exception:
                logger.exception("Artifacts MCP request crashed")

    async with anyio.create_task_group() as task_group:
        await task_group.start(run_server)
        await transport.handle_request(scope, receive, send)
        await transport.terminate()
