"""Which files can meaningfully carry a code-review defect.

Every miner needs this, so it lives in one place rather than three.

The motivating measurement: 59% of the first full `psf/requests` run sat on
non-Python files — CI YAML, changelogs, issue templates, a stray
`.coverage.*` artifact, and 78 KB of PEM certificate data. Phase 0 scoped
this project to Python, so those cases were out of scope by the project's
own definition, and several were on files no reviewer could
have flagged a defect in at all.

Two distinct failures were hiding in that number:

- **Out-of-scope cases.** A `no_defect` case on `.github/dependabot.yml`
  isn't wrong so much as meaningless — it measures a reviewer's behaviour
  on a file type the benchmark never claimed to cover.
- **Un-reviewable cases.** `requests/cacert.pem` (a 78 KB certificate
  bundle) and `AUTHORS.rst` (a name addition) were labelled `defect`
  because the PR containing them was reverted. No reviewer could find a
  bug there, because there is no bug there — the defect lived in a
  sibling file. Scoring against those punishes correct behaviour.

Filtering here rather than at scoring time is deliberate: a case that
cannot be answered correctly should never enter the dataset, so the
published case count means what it says.
"""

from __future__ import annotations

import posixpath

# Phase 0 scopes this project to Python. Widening this set is a real
# decision — per-language tuning of the "is this comment substantive"
# heuristics has to happen alongside it (see docs/DESIGN.md non-goals).
DEFAULT_CODE_EXTENSIONS = frozenset({".py"})

# Directories whose contents are documentation or examples rather than the
# code under review. `docs/conf.py` is Python, but a defect in Sphinx
# configuration is not the kind of defect this benchmark is about.
_EXCLUDED_DIRS = frozenset({"docs", "doc", "examples", "example", "site", "_build"})

# Files that are metadata about the project rather than part of it. Matched
# on the stem, case-insensitively, so `AUTHORS`, `AUTHORS.rst`, and
# `authors.md` all match.
_EXCLUDED_STEMS = frozenset(
    {
        "authors",
        "changelog",
        "changes",
        "codeowners",
        "contributing",
        "contributors",
        "history",
        "license",
        "licence",
        "notice",
        "readme",
        "security",
    }
)


def is_reviewable_code_path(
    file_path: str, *, extensions: frozenset[str] = DEFAULT_CODE_EXTENSIONS
) -> bool:
    """True if a defect in `file_path` is in scope for this benchmark.

    Conservative by design. A file wrongly excluded costs one case; a file
    wrongly included becomes a question with no right answer, which
    silently penalises whichever reviewer behaves correctly.
    """
    if not file_path:
        return False

    # GitHub always reports POSIX-style paths, regardless of the platform
    # this runs on — don't use os.path here.
    normalized = file_path.strip().lstrip("./")
    if not normalized:
        return False

    parts = normalized.split("/")
    name = parts[-1]

    if any(part.lower() in _EXCLUDED_DIRS for part in parts[:-1]):
        return False

    # Dotfiles and dot-directories are tooling config or stray artifacts
    # (`.coveragerc`, `.github/...`, a `.coverage.<host>.<pid>` file that
    # got committed by accident and showed up in the first run).
    if any(part.startswith(".") for part in parts):
        return False

    stem, _, ext = name.rpartition(".")
    if not stem:  # e.g. "Makefile" — no extension to match on
        return False

    if f".{ext}".lower() not in extensions:
        return False

    return stem.lower() not in _EXCLUDED_STEMS
