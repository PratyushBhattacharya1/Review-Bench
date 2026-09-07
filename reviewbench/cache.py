"""Disk cache for LLM responses, keyed on a hash of the request.

Built now rather than later, deliberately: 300 cases x several model configs
x several iterations is the kind of thing that quietly runs up an API bill,
and retrofitting a cache after the call sites exist is worse than designing
around one from the start.

The key is a hash of everything that could change the response (model,
prompt, and any other request parameters), so a cache hit is only ever
returned for a byte-identical request.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_CACHE_DIR = Path(".reviewbench_cache")


def cache_key(**request_parts: Any) -> str:
    """Hash the parts of a request that affect its response.

    `sort_keys=True` matters: without it, dict ordering could produce two
    different keys for the same logical request and every call would miss.
    """
    payload = json.dumps(request_parts, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class ResponseCache:
    """A content-addressed cache of model responses on disk.

    Values are JSON, so a cache written by one run is readable by the next
    — the point is to survive process restarts during iteration, not just
    to dedupe within a single run.
    """

    def __init__(self, cache_dir: str | Path = DEFAULT_CACHE_DIR, *, enabled: bool = True) -> None:
        self.cache_dir = Path(cache_dir)
        self.enabled = enabled
        self.hits = 0
        self.misses = 0

    def _path_for(self, key: str) -> Path:
        # Shard by the first two hex chars so a large cache doesn't become
        # one directory with tens of thousands of entries.
        return self.cache_dir / key[:2] / f"{key}.json"

    def get(self, key: str) -> Any | None:
        if not self.enabled:
            return None
        path = self._path_for(key)
        if not path.exists():
            self.misses += 1
            return None
        try:
            with path.open("r", encoding="utf-8") as f:
                value = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            # A corrupt entry should degrade to a miss, not kill the run.
            logger.warning("Discarding unreadable cache entry %s: %s", path, e)
            self.misses += 1
            return None
        self.hits += 1
        return value

    def set(self, key: str, value: Any) -> None:
        if not self.enabled:
            return
        path = self._path_for(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            json.dump(value, f)

    @property
    def stats(self) -> dict[str, int]:
        return {"hits": self.hits, "misses": self.misses}
