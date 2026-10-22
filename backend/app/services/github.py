import hashlib
import logging
import time
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
# Repo search filters the user's own list: up to 1000 repos, reused for a minute.
ACCESSIBLE_REPOS_MAX_PAGES = 10
ACCESSIBLE_REPOS_TTL_SECONDS = 60.0
_ACCESSIBLE_REPOS_CACHE: dict[str, tuple[float, list[dict[str, Any]]]] = {}


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
        terms = query.lower().split()
        if terms:
            # Search only repos the user can reach (own, collaborator, org):
            # GitHub's /search/repositories spans every public repo.
            matches = [
                r
                for r in await self._accessible_repos()
                if all(
                    term in f"{r['full_name']} {r.get('description') or ''}".lower()
                    for term in terms
                )
            ]
            start = (page - 1) * per_page
            return GitHubReposResponse(
                items=[self._repo(r) for r in matches[start : start + per_page]],
                has_more=len(matches) > start + per_page,
            )

        async with httpx.AsyncClient(timeout=10.0) as client:
            raw_repos = await self._user_repos_page(client, page, per_page)
        return GitHubReposResponse(
            items=[self._repo(r) for r in raw_repos],
            has_more=len(raw_repos) == per_page,
        )

    async def _user_repos_page(
        self, client: httpx.AsyncClient, page: int, per_page: int
    ) -> list[dict[str, Any]]:
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
        data: list[dict[str, Any]] = response.json()
        return data

    async def _accessible_repos(self) -> list[dict[str, Any]]:
        # Searching re-runs per keystroke, so keep the user's full list briefly.
        key = hashlib.sha256(self._headers["Authorization"].encode()).hexdigest()
        now = time.monotonic()
        cached = _ACCESSIBLE_REPOS_CACHE.get(key)
        if cached and now - cached[0] < ACCESSIBLE_REPOS_TTL_SECONDS:
            return cached[1]
        repos: list[dict[str, Any]] = []
        async with httpx.AsyncClient(timeout=10.0) as client:
            for page in range(1, ACCESSIBLE_REPOS_MAX_PAGES + 1):
                batch = await self._user_repos_page(client, page, 100)
                repos.extend(batch)
                if len(batch) < 100:
                    break
        for stale in [
            k
            for k, (at, _) in _ACCESSIBLE_REPOS_CACHE.items()
            if now - at >= ACCESSIBLE_REPOS_TTL_SECONDS
        ]:
            del _ACCESSIBLE_REPOS_CACHE[stale]
        _ACCESSIBLE_REPOS_CACHE[key] = (now, repos)
        return repos

    @staticmethod
    def _repo(r: dict[str, Any]) -> GitHubRepo:
        return GitHubRepo(
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

    async def create_repo_webhook(
        self, repo: str, url: str, secret: str, events: list[str]
    ) -> int:
        # Needs admin:repo_hook (classic token) or Webhooks: write (fine-grained).
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                f"{GITHUB_API_BASE}/repos/{repo}/hooks",
                json={
                    "name": "web",
                    "active": True,
                    "events": events,
                    "config": {
                        "url": url,
                        "content_type": "json",
                        "secret": secret,
                        "insecure_ssl": "0",
                    },
                },
                headers=self._headers,
            )
        self._check_auth(response)
        if response.status_code in (403, 404):
            raise GitHubException(
                "GitHub token can't manage webhooks on this repository",
                status_code=response.status_code,
            )
        if response.status_code != 201:
            message = ""
            try:
                errors = response.json().get("errors") or []
                message = "; ".join(
                    str(e.get("message", "")) for e in errors if isinstance(e, dict)
                )
            except ValueError:
                pass
            logger.warning(
                "GitHub webhook creation returned %d: %s",
                response.status_code,
                response.text[:200],
            )
            raise GitHubException(
                message or "GitHub refused to create the webhook",
                status_code=response.status_code,
            )
        hook_id: int = response.json()["id"]
        return hook_id

    async def delete_repo_webhook(self, repo: str, hook_id: int) -> None:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.delete(
                f"{GITHUB_API_BASE}/repos/{repo}/hooks/{hook_id}",
                headers=self._headers,
            )
        self._check_auth(response)
        # 404: already removed on GitHub's side.
        if response.status_code not in (204, 404):
            raise GitHubException(
                "Failed to remove the GitHub webhook", status_code=response.status_code
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
