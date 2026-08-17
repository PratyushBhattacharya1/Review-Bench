"""Thin GitHub REST API client: pagination and rate-limit handling.

Deliberately not a full GitHub SDK. Miners only need a handful of
endpoints (list PRs, list PR files, list PR review comments, list PRs
associated with a commit), so this wraps `requests` with the two things
that are easy to get wrong: following `Link` header pagination correctly,
and backing off on rate limits instead of crashing mid-run.
"""

from __future__ import annotations

import time
from typing import Any, Iterator

import requests

API_ROOT = "https://api.github.com"


class GitHubError(RuntimeError):
    pass


class GitHubClient:
    def __init__(self, token: str | None = None, *, sleep_fn=time.sleep) -> None:
        self._session = requests.Session()
        headers = {"Accept": "application/vnd.github+json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._session.headers.update(headers)
        self._sleep = sleep_fn

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """GET a single (non-paginated) resource and return the parsed JSON body."""
        resp = self._request(path, params)
        return resp.json()

    def get_paginated(
        self, path: str, params: dict[str, Any] | None = None, *, per_page: int = 100
    ) -> Iterator[dict]:
        """Yield items across all pages of a list endpoint, following the
        `Link` header rather than assuming a fixed page count.
        """
        params = dict(params or {})
        params.setdefault("per_page", per_page)

        url: str | None = f"{API_ROOT}{path}"
        while url:
            resp = self._request_url(url, params if url.endswith(path) or "?" not in url else None)
            body = resp.json()
            if not isinstance(body, list):
                raise GitHubError(f"Expected a list response from {url}, got {type(body)}")
            yield from body
            url = _next_link(resp.headers.get("Link"))
            params = None  # subsequent URLs from Link header already carry query params

    def search_issues(
        self, query: str, *, per_page: int = 100, max_results: int | None = None
    ) -> Iterator[dict]:
        """Yield issues/PRs matching a GitHub search query.

        Separate from `get_paginated` because the search endpoints wrap
        results in an envelope (`{total_count, items: [...]}`) rather than
        returning a bare list, and paginate by `page` number rather than by
        `Link` header alone.

        Search exists here because some things are needle-in-haystack: a
        repo's revert PRs may be thousands of PRs deep in its history, and
        walking the whole list to find a handful of them is both slow and a
        good way to burn a rate-limit budget.
        """
        page = 1
        yielded = 0
        while True:
            body = self.get(
                "/search/issues", {"q": query, "per_page": per_page, "page": page}
            )
            items = body.get("items", [])
            if not items:
                return
            for item in items:
                yield item
                yielded += 1
                if max_results is not None and yielded >= max_results:
                    return
            # The search API caps out at 1000 results regardless of matches.
            if len(items) < per_page or yielded >= 1000:
                return
            page += 1

    def _request(self, path: str, params: dict[str, Any] | None) -> requests.Response:
        return self._request_url(f"{API_ROOT}{path}", params)

    def _request_url(self, url: str, params: dict[str, Any] | None) -> requests.Response:
        while True:
            resp = self._session.get(url, params=params, timeout=30)
            if resp.status_code == 403 and _is_rate_limited(resp):
                self._wait_for_rate_limit(resp)
                continue
            if resp.status_code == 404:
                raise GitHubError(f"404 Not Found: {url}")
            if not resp.ok:
                raise GitHubError(f"GitHub API error {resp.status_code} for {url}: {resp.text[:500]}")
            return resp

    def _wait_for_rate_limit(self, resp: requests.Response) -> None:
        reset = resp.headers.get("X-RateLimit-Reset")
        retry_after = resp.headers.get("Retry-After")
        if retry_after is not None:
            wait_s = float(retry_after)
        elif reset is not None:
            wait_s = max(0.0, float(reset) - time.time()) + 1
        else:
            wait_s = 60.0
        self._sleep(wait_s)


def _is_rate_limited(resp: requests.Response) -> bool:
    remaining = resp.headers.get("X-RateLimit-Remaining")
    return remaining == "0" or "rate limit" in resp.text.lower()


def _next_link(link_header: str | None) -> str | None:
    """Parse the `Link` response header for the `rel="next"` URL, per
    GitHub's pagination convention (RFC 5988).
    """
    if not link_header:
        return None
    for part in link_header.split(","):
        segments = part.split(";")
        if len(segments) < 2:
            continue
        url_part = segments[0].strip()
        rel_part = segments[1].strip()
        if rel_part == 'rel="next"' and url_part.startswith("<") and url_part.endswith(">"):
            return url_part[1:-1]
    return None
