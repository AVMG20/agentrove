from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.deps import get_db, get_user_service
from app.core.security import get_current_user
from app.models.db_models.user import User
from app.models.schemas.usage import ClaudeUsageResponse
from app.services.claude_usage import claude_usage_service, resolve_usage_source
from app.services.exceptions import UserException
from app.services.user import UserService

router = APIRouter()
settings = get_settings()


@router.get("/claude", response_model=ClaudeUsageResponse)
async def get_claude_usage(
    refresh: bool = Query(default=False),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    user_service: UserService = Depends(get_user_service),
) -> ClaudeUsageResponse:
    # 5-hour / weekly usage of the Claude proxy the user's agents run through.
    try:
        user_settings = await user_service.get_user_settings(current_user.id, db=db)
        custom_env_vars = user_settings.custom_env_vars
    except UserException:
        custom_env_vars = None
    source = resolve_usage_source(settings, custom_env_vars)
    return await claude_usage_service.get_usage(
        str(current_user.id), source, refresh=refresh
    )
