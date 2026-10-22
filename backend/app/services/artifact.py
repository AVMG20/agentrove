from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db_models.artifact import Artifact
from app.models.db_models.chat import Chat
from app.models.schemas.artifact import ArtifactCreate, ArtifactUpdate
from app.services.db import BaseDbService
from app.services.exceptions import ArtifactException, ChatException, ErrorCode

# Edits to these bump the version; visibility changes don't.
CONTENT_FIELDS = frozenset({"title", "html"})


class ArtifactService(BaseDbService[Artifact]):
    @staticmethod
    def _not_found(artifact_id: UUID) -> ArtifactException:
        return ArtifactException(
            "Artifact not found",
            details={"artifact_id": str(artifact_id)},
            status_code=404,
        )

    async def _get_owned(
        self, db: AsyncSession, artifact_id: UUID, user_id: UUID
    ) -> Artifact:
        result = await db.execute(
            select(Artifact).filter(
                Artifact.id == artifact_id, Artifact.user_id == user_id
            )
        )
        artifact: Artifact | None = result.scalar_one_or_none()
        if not artifact:
            raise self._not_found(artifact_id)
        return artifact

    @staticmethod
    async def _validate_chat(db: AsyncSession, chat_id: UUID, user_id: UUID) -> None:
        result = await db.execute(
            select(Chat.id).filter(
                Chat.id == chat_id,
                Chat.user_id == user_id,
                Chat.deleted_at.is_(None),
            )
        )
        if not result.one_or_none():
            raise ChatException(
                "Chat not found",
                error_code=ErrorCode.CHAT_NOT_FOUND,
                details={"chat_id": str(chat_id)},
                status_code=404,
            )

    async def list_artifacts(self, user_id: UUID) -> list[Artifact]:
        async with self.session_factory() as db:
            result = await db.execute(
                select(Artifact)
                .filter(Artifact.user_id == user_id)
                .order_by(Artifact.updated_at.desc())
            )
            return list(result.scalars().all())

    async def get_artifact(self, artifact_id: UUID, user_id: UUID) -> Artifact:
        async with self.session_factory() as db:
            return await self._get_owned(db, artifact_id, user_id)

    async def get_viewable_artifact(
        self, artifact_id: UUID, user_id: UUID | None, *, view_granted: bool = False
    ) -> Artifact:
        # Private artifacts 404 for everyone but the owner (or a view token the
        # owner was issued), so their existence doesn't leak.
        async with self.session_factory() as db:
            artifact = await db.get(Artifact, artifact_id)
        if not artifact or not (
            artifact.is_public or view_granted or artifact.user_id == user_id
        ):
            raise self._not_found(artifact_id)
        return artifact

    async def create_artifact(self, user_id: UUID, data: ArtifactCreate) -> Artifact:
        async with self.session_factory() as db:
            if data.chat_id is not None:
                await self._validate_chat(db, data.chat_id, user_id)
            artifact = Artifact(user_id=user_id, **data.model_dump())
            db.add(artifact)
            await db.commit()
            await db.refresh(artifact)
            return artifact

    async def update_artifact(
        self, artifact_id: UUID, user_id: UUID, data: ArtifactUpdate
    ) -> Artifact:
        changes = data.model_dump(exclude_unset=True)
        async with self.session_factory() as db:
            artifact = await self._get_owned(db, artifact_id, user_id)
            content_changed = any(
                getattr(artifact, field) != value
                for field, value in changes.items()
                if field in CONTENT_FIELDS
            )
            for field, value in changes.items():
                setattr(artifact, field, value)
            if content_changed:
                artifact.version += 1
            await db.commit()
            await db.refresh(artifact)
            return artifact

    async def delete_artifact(self, artifact_id: UUID, user_id: UUID) -> None:
        async with self.session_factory() as db:
            artifact = await self._get_owned(db, artifact_id, user_id)
            await db.delete(artifact)
            await db.commit()
