import logging
from collections.abc import Collection
from datetime import datetime
from typing import Any, Literal

import httpx

from app.models.schemas.github import (
    CreatePullRequestRequest,
    GitHubActivityEvent,
    CreatePullRequestResponse,
    GitHubCollaborator,
    GitHubPRListResponse,
    GitHubPullRequest,
    GitHubRepo,
    GitHubReposResponse,
)
from app.services.exceptions import ErrorCode, GitHubException

logger = logging.getLogger(__name__)

GITHUB_API_BASE = "https://api.github.com"
# Per-endpoint page for activity polling — more than one poll interval's worth
# of new items on any sane repo; the dispatcher caps runs per poll anyway.
ACTIVITY_PAGE_SIZE = 50


def _parse_github_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class GitHubService:
    def __init__(self, token: str) -> None:
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def _check_auth(self, response: httpx.Response) -> None:
        if response.status_code == 401:
            # Reserve HTTP 401 for this app's own auth/session failures so the frontend
            # does not treat an invalid GitHub PAT as a reason to log the user out.
            raise GitHubException(
                "GitHub token is invalid or expired",
                error_code=ErrorCode.GITHUB_TOKEN_INVALID,
                status_code=400,
            )

    def _check_response(self, response: httpx.Response) -> None:
        self._check_auth(response)
        if response.status_code != 200:
            logger.warning(
                "GitHub API returned %d: %s",
                response.status_code,
                response.text[:200],
            )
            raise GitHubException("GitHub API request failed")

    async def list_repositories(
        self, query: str, page: int, per_page: int
    ) -> GitHubReposResponse:
        stripped = query.strip()
        async with httpx.AsyncClient(timeout=10.0) as client:
            if stripped:
                response = await client.get(
                    f"{GITHUB_API_BASE}/search/repositories",
                    params={
                        "q": stripped,
                        "sort": "updated",
                        "order": "desc",
                        "per_page": per_page,
                        "page": page,
                    },
                    headers=self._headers,
                )
            else:
                response = await client.get(
                    f"{GITHUB_API_BASE}/user/repos",
                    params={
                        "sort": "pushed",
                        "direction": "desc",
                        "per_page": per_page,
                        "page": page,
                        "affiliation": "owner,collaborator,organization_member",
                    },
                    headers=self._headers,
                )

        self._check_response(response)

        data = response.json()
        raw_repos = data.get("items", data) if isinstance(data, dict) else data

        repos = [
            GitHubRepo(
                name=r["name"],
                full_name=r["full_name"],
                description=r.get("description"),
                language=r.get("language"),
                html_url=r["html_url"],
                clone_url=r["clone_url"],
                private=r.get("private", False),
                pushed_at=r.get("pushed_at"),
                stargazers_count=r.get("stargazers_count", 0),
            )
            for r in raw_repos
        ]

        return GitHubReposResponse(items=repos, has_more=len(raw_repos) == per_page)

    async def list_pull_requests(
        self,
        owner: str,
        repo: str,
        head: str | None = None,
        state: Literal["open", "closed", "all"] = "open",
    ) -> GitHubPRListResponse:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                f"{GITHUB_API_BASE}/repos/{owner}/{repo}/pulls",
                params={
                    "state": state,
                    "per_page": 30,
                    "sort": "updated",
                    "direction": "desc",
                    **({"head": head} if head else {}),
                },
                headers=self._headers,
            )

        self._check_response(response)

        raw_prs = response.json()
        prs = [
            GitHubPullRequest(
                number=pr["number"],
                title=pr["title"],
                body=pr.get("body"),
                state=pr["state"],
                html_url=pr["html_url"],
                head={
                    "ref": pr["head"]["ref"],
                    "sha": pr["head"]["sha"],
                    "repo": {
                        "full_name": (
                            pr["head"]["repo"]["full_name"]
                            if pr["head"].get("repo")
                            else ""
                        )
                    },
                },
                base={"ref": pr["base"]["ref"]},
                user={
                    "login": pr["user"]["login"],
                    "avatar_url": pr["user"].get("avatar_url", ""),
                },
                draft=pr.get("draft", False),
                review_comments=pr.get("review_comments", 0),
                merged=pr.get("merged_at") is not None,
            )
            for pr in raw_prs
        ]

        return GitHubPRListResponse(items=prs)

    async def create_pull_request(
        self, request: CreatePullRequestRequest
    ) -> CreatePullRequestResponse:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                f"{GITHUB_API_BASE}/repos/{request.owner}/{request.repo}/pulls",
                json={
                    "title": request.title,
                    "body": request.body,
                    "head": request.head,
                    "base": request.base,
                },
                headers=self._headers,
            )

            self._check_auth(response)
            if response.status_code not in (200, 201):
                logger.warning(
                    "GitHub API returned %d: %s",
                    response.status_code,
                    response.text[:200],
                )
                try:
                    detail = response.json().get(
                        "message", "Failed to create pull request"
                    )
                except (ValueError, KeyError):
                    detail = "Failed to create pull request"
                raise GitHubException(detail)

            pr_data = response.json()

            reviewer_warning = None
            if request.reviewers:
                try:
                    reviewer_resp = await client.post(
                        f"{GITHUB_API_BASE}/repos/{request.owner}/{request.repo}/pulls/{pr_data['number']}/requested_reviewers",
                        json={"reviewers": request.reviewers},
                        headers=self._headers,
                    )
                    if reviewer_resp.status_code not in (200, 201):
                        reviewer_warning = "Failed to assign reviewers"
                        logger.warning(
                            "Failed to assign reviewers to PR #%d: %s",
                            pr_data["number"],
                            reviewer_resp.text[:200],
                        )
                except httpx.HTTPError:
                    reviewer_warning = "Failed to assign reviewers"
                    logger.warning(
                        "Failed to assign reviewers to PR #%d", pr_data["number"]
                    )

        return CreatePullRequestResponse(
            number=pr_data["number"],
            html_url=pr_data["html_url"],
            title=pr_data["title"],
            reviewer_warning=reviewer_warning,
        )

    async def list_collaborators(
        self, owner: str, repo: str
    ) -> list[GitHubCollaborator]:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                f"{GITHUB_API_BASE}/repos/{owner}/{repo}/collaborators",
                params={"per_page": 50},
                headers=self._headers,
            )

        self._check_auth(response)
        # 403/404 are expected when user lacks push access to the repo
        if response.status_code in (403, 404):
            return []
        if response.status_code != 200:
            logger.warning(
                "GitHub collaborators API returned %d: %s",
                response.status_code,
                response.text[:200],
            )
            raise GitHubException("Failed to load collaborators")

        return [
            GitHubCollaborator(login=c["login"], avatar_url=c.get("avatar_url", ""))
            for c in response.json()
        ]

    async def get_authenticated_login(self) -> str:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                f"{GITHUB_API_BASE}/user", headers=self._headers
            )
        self._check_response(response)
        login: str = response.json()["login"]
        return login

    async def list_activity(
        self,
        repo: str,
        events: Collection[str],
        since: datetime,
        exclude_author: str | None = None,
    ) -> list[GitHubActivityEvent]:
        # Polling (not webhooks): works with a plain PAT, no public URL or repo
        # admin rights needed. Returns items created strictly after `since`,
        # oldest first; exclude_author drops our own activity so an automation
        # that comments on issues can't trigger itself.
        found: list[GitHubActivityEvent] = []
        async with httpx.AsyncClient(timeout=15.0) as client:
            if {"pull_request_opened", "issue_opened"} & set(events):
                items = await self._get_json(
                    client,
                    f"/repos/{repo}/issues",
                    {
                        "state": "all",
                        "sort": "created",
                        "direction": "desc",
                        "since": since.isoformat(),
                        "per_page": ACTIVITY_PAGE_SIZE,
                    },
                )
                for item in items:
                    kind = (
                        "pull_request_opened"
                        if "pull_request" in item
                        else "issue_opened"
                    )
                    if kind in events:
                        found.append(self._activity(kind, repo, item, item))
            if "issue_comment_created" in events:
                comments = await self._get_json(
                    client,
                    f"/repos/{repo}/issues/comments",
                    {
                        "sort": "created",
                        "direction": "desc",
                        "since": since.isoformat(),
                        "per_page": ACTIVITY_PAGE_SIZE,
                    },
                )
                for comment in comments:
                    number = int(comment["issue_url"].rsplit("/", 1)[-1])
                    found.append(
                        self._activity(
                            "issue_comment_created",
                            repo,
                            comment,
                            {"number": number, "title": f"Comment on #{number}"},
                        )
                    )
            if "release_published" in events:
                releases = await self._get_json(
                    client, f"/repos/{repo}/releases", {"per_page": 20}
                )
                for release in releases:
                    if release.get("draft") or not release.get("published_at"):
                        continue
                    found.append(
                        self._activity(
                            "release_published",
                            repo,
                            {**release, "created_at": release["published_at"]},
                            {"title": release.get("name") or release["tag_name"]},
                        )
                    )
        return sorted(
            (
                e
                for e in found
                if e.created_at > since
                and (exclude_author is None or e.author != exclude_author)
            ),
            key=lambda e: e.created_at,
        )

    async def _get_json(
        self, client: httpx.AsyncClient, path: str, params: dict[str, Any]
    ) -> list[dict[str, Any]]:
        response = await client.get(
            f"{GITHUB_API_BASE}{path}", params=params, headers=self._headers
        )
        self._check_auth(response)
        if response.status_code == 404:
            raise GitHubException(
                "Repository not found or the GitHub token can't access it",
                status_code=404,
            )
        self._check_response(response)
        data: list[dict[str, Any]] = response.json()
        return data

    @staticmethod
    def _activity(
        event: str,
        repo: str,
        item: dict[str, Any],
        summary: dict[str, Any],
    ) -> GitHubActivityEvent:
        return GitHubActivityEvent(
            event=event,
            repo=repo,
            number=summary.get("number"),
            title=summary.get("title") or "",
            url=item["html_url"],
            author=(item.get("user") or item.get("author") or {}).get(
                "login", "unknown"
            ),
            body=item.get("body"),
            created_at=_parse_github_time(item["created_at"]),
        )
