"""Shared test fixtures. Miner tests use FakeClient instead of hitting the
network or mocking HTTP — the miners only depend on GitHubClient's
`.get` / `.get_paginated` interface, so a fake implementing that interface
tests the miner logic directly without coupling to transport details.
"""

from __future__ import annotations

from typing import Any, Iterator


class FakeClient:
    def __init__(self, paginated: dict[str, list[dict]] | None = None, resources: dict[str, Any] | None = None) -> None:
        self.paginated = paginated or {}
        self.resources = resources or {}
        self.calls: list[str] = []

    def get(self, path: str, params: dict | None = None) -> Any:
        self.calls.append(path)
        if path not in self.resources:
            raise AssertionError(f"FakeClient.get called with unregistered path: {path}")
        return self.resources[path]

    def get_paginated(self, path: str, params: dict | None = None, *, per_page: int = 100) -> Iterator[dict]:
        self.calls.append(path)
        yield from self.paginated.get(path, [])
