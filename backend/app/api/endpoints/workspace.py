import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import SQLAlchemyError

from app.core.deps import get_workspace_service
from app.core.security import get_current_user
from app.models.db_models.user import User
from app.models.schemas.pagination import PaginatedResponse
from app.models.schemas.workspace import (
    Workspace as WorkspaceSchema,
    WorkspaceCreate,
    WorkspaceDiskUsage,
    WorkspaceResources,
    WorkspaceUpdate,
)
from app.services.exceptions import WorkspaceException
from app.services.workspace import WorkspaceService

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post(
    "",
    response_model=WorkspaceSchema,
    status_code=status.HTTP_201_CREATED,
)
async def create_workspace(
    data: WorkspaceCreate,
    current_user: User = Depends(get_current_user),
    workspace_service: WorkspaceService = Depends(get_workspace_service),
) -> WorkspaceSchema:
    try:
        return await workspace_service.create_workspace(current_user, data)
    except WorkspaceException as e:
        raise HTTPException(status_code=e.status_code, detail=str(e)) from e
    except SQLAlchemyError as e:
        logger.error("Database error creating workspace: %s", e, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database error while creating workspace",
        ) from e


@router.get("", response_model=PaginatedResponse[WorkspaceSchema])
async def list_workspaces(
    current_user: User = Depends(get_current_user),
    workspace_service: WorkspaceService = Depends(get_workspace_service),
) -> PaginatedResponse[WorkspaceSchema]:
    return await workspace_service.get_user_workspaces(current_user)


@router.get("/disk-usage", response_model=list[WorkspaceDiskUsage])
async def get_workspaces_disk_usage(
    refresh: bool = False,
    current_user: User = Depends(get_current_user),
    workspace_service: WorkspaceService = Depends(get_workspace_service),
) -> list[WorkspaceDiskUsage]:
    return await workspace_service.get_disk_usage(current_user, refresh=refresh)


@router.get("/{workspace_id}", response_model=WorkspaceSchema)
async def get_workspace(
    workspace_id: UUID,
    current_user: User = Depends(get_current_user),
    workspace_service: WorkspaceService = Depends(get_workspace_service),
) -> WorkspaceSchema:
    try:
        return await workspace_service.get_workspace(workspace_id, current_user)
    except WorkspaceException as e:
        raise HTTPException(status_code=e.status_code, detail=str(e)) from e


@router.patch("/{workspace_id}", response_model=WorkspaceSchema)
async def update_workspace(
    workspace_id: UUID,
    data: WorkspaceUpdate,
    current_user: User = Depends(get_current_user),
    workspace_service: WorkspaceService = Depends(get_workspace_service),
) -> WorkspaceSchema:
    try:
        return await workspace_service.update_workspace(
            workspace_id, current_user, data
        )
    except WorkspaceException as e:
        raise HTTPException(status_code=e.status_code, detail=str(e)) from e


@router.get("/{workspace_id}/resources", response_model=WorkspaceResources)
async def get_workspace_resources(
    workspace_id: UUID,
    current_user: User = Depends(get_current_user),
    workspace_service: WorkspaceService = Depends(get_workspace_service),
) -> WorkspaceResources:
    try:
        return await workspace_service.get_workspace_resources(
            workspace_id, current_user
        )
    except WorkspaceException as e:
        raise HTTPException(status_code=e.status_code, detail=str(e)) from e


@router.delete("/{workspace_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_workspace(
    workspace_id: UUID,
    current_user: User = Depends(get_current_user),
    workspace_service: WorkspaceService = Depends(get_workspace_service),
) -> None:
    try:
        await workspace_service.delete_workspace(workspace_id, current_user)
    except WorkspaceException as e:
        raise HTTPException(status_code=e.status_code, detail=str(e)) from e
