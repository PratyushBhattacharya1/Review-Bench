"""Path-filter tests.

Most cases below are real paths from the first psf/requests run, where 59%
of the dataset turned out to sit on files outside the project's own
Python-only scope.
"""

import pytest

from reviewbench.paths import is_reviewable_code_path


@pytest.mark.parametrize(
    "path",
    [
        "src/requests/adapters.py",
        "requests/utils.py",
        "tests/test_requests.py",
        "setup.py",
        "requests/packages/urllib3/connectionpool.py",
    ],
)
def test_python_source_is_reviewable(path):
    assert is_reviewable_code_path(path)


@pytest.mark.parametrize(
    "path",
    [
        "requests/cacert.pem",  # 78 KB certificate bundle
        "AUTHORS.rst",
        "README.rst",
        "HISTORY.md",
        "docs/index.rst",
        "docs/conf.py",  # Python, but documentation config
        "docs/user/advanced.rst",
        ".coverage.enoch.2677669.XurTNrNx",  # stray artifact, committed by accident
        ".coveragerc",
        ".github/workflows/codeql-analysis.yml",
        ".github/CONTRIBUTING.md",
        ".github/CODEOWNERS",
        "pyproject.toml",
        "requirements.txt",
        "examples/demo.py",
        "Makefile",
    ],
)
def test_non_code_and_out_of_scope_paths_are_rejected(path):
    assert not is_reviewable_code_path(path)


@pytest.mark.parametrize("path", ["", "   ", "./", None])
def test_empty_paths_are_rejected(path):
    assert not is_reviewable_code_path(path or "")


def test_metadata_stems_rejected_regardless_of_extension():
    assert not is_reviewable_code_path("changelog.py")
    assert not is_reviewable_code_path("LICENSE.py")


def test_leading_dot_slash_is_tolerated():
    assert is_reviewable_code_path("./requests/models.py")


def test_extension_set_is_overridable_for_future_languages():
    assert not is_reviewable_code_path("src/main.go")
    assert is_reviewable_code_path("src/main.go", extensions=frozenset({".go"}))
