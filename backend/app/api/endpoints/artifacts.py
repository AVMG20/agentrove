import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse
from sqlalchemy.exc import SQLAlchemyError

from app.core.deps import (
    ArtifactActor,
    get_artifact_actor,
    get_artifact_service,
    get_optional_artifact_viewer,
)
from app.core.security import (
    ARTIFACT_VIEW_AUDIENCE,
    create_artifact_view_token,
    decode_scoped_token,
)
from app.models.db_models.user import User
from app.models.schemas.artifact import (
    Artifact as ArtifactSchema,
    ArtifactCreate,
    ArtifactDetail,
    ArtifactUpdate,
    ArtifactView,
)
from app.services.artifact import ArtifactService
from app.services.exceptions import ArtifactException, ChatException

router = APIRouter()
logger = logging.getLogger(__name__)

# Artifact HTML is arbitrary agent-written markup served from the app's own
# origin. The CSP sandbox (no allow-same-origin) gives the document an opaque
# origin even when opened directly, so it can't read the app's storage or
# cookies, or call the API as the viewer. The viewer's iframe sandbox repeats it.
ARTIFACT_CONTENT_POLICY = (
    "sandbox allow-scripts allow-forms allow-modals allow-popups "
    "allow-popups-to-escape-sandbox allow-downloads"
)


def _forbid_agent(actor: ArtifactActor, action: str) -> None:
    if actor.agent_scoped:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Only the user can {action} an artifact",
        )


@router.get("", response_model=list[ArtifactSchema])
async def list_artifacts(
    actor: ArtifactActor = Depends(get_artifact_actor),
    artifact_service: ArtifactService = Depends(get_artifact_service),
) -> list[ArtifactSchema]:
    artifacts = await artifact_service.list_artifacts(actor.user.id)
    return [ArtifactSchema.model_validate(artifact) for artifact in artifacts]


@router.post("", response_model=ArtifactDetail, status_code=status.HTTP_201_CREATED)
async def create_artifact(
    data: ArtifactCreate,
    actor: ArtifactActor = Depends(get_artifact_actor),
    artifact_service: ArtifactService = Depends(get_artifact_service),
) -> ArtifactDetail:
    try:
        artifact = await artifact_service.create_artifact(actor.user.id, data)
    except ChatException as e:
        raise HTTPException(status_code=e.status_code, detail=str(e)) from e
    except SQLAlchemyError as e:
        logger.error("Database error creating artifact: %s", e, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database error while creating artifact",
        ) from e
    return ArtifactDetail.model_validate(artifact)


@router.get("/{artifact_id}", response_model=ArtifactDetail)
async def get_artifact(
    artifact_id: UUID,
    actor: ArtifactActor = Depends(get_artifact_actor),
    artifact_service: ArtifactService = Depends(get_artifact_service),
) -> ArtifactDetail:
    try:
        artifact = await artifact_service.get_artifact(artifact_id, actor.user.id)
    except ArtifactException as e:
        raise HTTPException(status_code=e.status_code, detail=str(e)) from e
    return ArtifactDetail.model_validate(artifact)


@router.patch("/{artifact_id}", response_model=ArtifactDetail)
async def update_artifact(
    artifact_id: UUID,
    data: ArtifactUpdate,
    actor: ArtifactActor = Depends(get_artifact_actor),
    artifact_service: ArtifactService = Depends(get_artifact_service),
) -> ArtifactDetail:
    if "is_public" in data.model_fields_set:
        _forbid_agent(actor, "change the visibility of")
    try:
        artifact = await artifact_service.update_artifact(
            artifact_id, actor.user.id, data
        )
    except ArtifactException as e:
        raise HTTPException(status_code=e.status_code, detail=str(e)) from e
    return ArtifactDetail.model_validate(artifact)


@router.delete("/{artifact_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_artifact(
    artifact_id: UUID,
    actor: ArtifactActor = Depends(get_artifact_actor),
    artifact_service: ArtifactService = Depends(get_artifact_service),
) -> None:
    _forbid_agent(actor, "delete")
    try:
        await artifact_service.delete_artifact(artifact_id, actor.user.id)
    except ArtifactException as e:
        raise HTTPException(status_code=e.status_code, detail=str(e)) from e


@router.get("/{artifact_id}/view", response_model=ArtifactView)
async def view_artifact(
    artifact_id: UUID,
    viewer: User | None = Depends(get_optional_artifact_viewer),
    artifact_service: ArtifactService = Depends(get_artifact_service),
) -> ArtifactView:
    try:
        artifact = await artifact_service.get_viewable_artifact(
            artifact_id, viewer.id if viewer else None
        )
    except ArtifactException as e:
        raise HTTPException(status_code=e.status_code, detail=str(e)) from e
    # The version busts the browser cache after edits; a private artifact's
    # iframe can't send the bearer header, so it gets a view token instead.
    content_path = f"/artifacts/{artifact.id}/content?v={artifact.version}"
    if not artifact.is_public:
        content_path += f"&view_token={create_artifact_view_token(artifact.id)}"
    return ArtifactView(
        **ArtifactSchema.model_validate(artifact).model_dump(),
        is_owner=viewer is not None and viewer.id == artifact.user_id,
        content_path=content_path,
    )


@router.get("/{artifact_id}/content", response_class=HTMLResponse)
async def get_artifact_content(
    artifact_id: UUID,
    request: Request,
    view_token: str | None = Query(None),
    viewer: User | None = Depends(get_optional_artifact_viewer),
    artifact_service: ArtifactService = Depends(get_artifact_service),
) -> HTMLResponse:
    granted = bool(view_token) and decode_scoped_token(
        view_token or "", ARTIFACT_VIEW_AUDIENCE
    ) == str(artifact_id)
    try:
        artifact = await artifact_service.get_viewable_artifact(
            artifact_id, viewer.id if viewer else None, view_granted=granted
        )
    except ArtifactException as e:
        raise HTTPException(status_code=e.status_code, detail=str(e)) from e
    request.state.allow_framing = True
    return HTMLResponse(
        artifact.html,
        headers={
            "Content-Security-Policy": ARTIFACT_CONTENT_POLICY,
            # Keeps the view token in the URL from leaking to sites the page
            # links to or loads from.
            "Referrer-Policy": "no-referrer",
            "Cache-Control": "no-cache" if artifact.is_public else "private, no-store",
        },
    )
