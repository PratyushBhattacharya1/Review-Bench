from tests.conftest import FakeClient

from reviewbench.miners.clean_pr_miner import mine_clean_hunks

BIG_PATCH = "@@ -1,3 +1,5 @@\n def f(items):\n-    return items[0]\n+    if not items:\n+        return None\n+    return items[0]\n"


def _pr(number=5, title="Add empty check", merged=True) -> dict:
    return {
        "number": number,
        "merged_at": "2024-01-01T00:00:00Z" if merged else None,
        "title": title,
        "html_url": f"https://github.com/owner/repo/pull/{number}",
        "base": {"sha": "base-sha"},
        "head": {"sha": "head-sha"},
    }


def _client(pr, comments, files) -> FakeClient:
    return FakeClient(
        paginated={
            "/repos/owner/repo/pulls": [pr],
            f"/repos/owner/repo/pulls/{pr['number']}/comments": comments,
            f"/repos/owner/repo/pulls/{pr['number']}/files": files,
        }
    )


def test_clean_hunk_is_yielded():
    client = _client(_pr(), [], [{"filename": "foo.py", "patch": BIG_PATCH}])

    cases = list(mine_clean_hunks(client, "owner", "repo"))

    assert len(cases) == 1
    assert cases[0].file_path == "foo.py"
    assert cases[0].label == "no_defect"
    assert cases[0].base_commit_sha == "base-sha"


def test_file_with_substantive_comment_is_excluded():
    comments = [{"path": "foo.py", "body": "This will throw when items is empty."}]
    client = _client(_pr(), comments, [{"filename": "foo.py", "patch": BIG_PATCH}])

    assert list(mine_clean_hunks(client, "owner", "repo")) == []


def test_file_with_only_trivial_comment_is_still_clean():
    comments = [{"path": "foo.py", "body": "LGTM"}]
    client = _client(_pr(), comments, [{"filename": "foo.py", "patch": BIG_PATCH}])

    assert len(list(mine_clean_hunks(client, "owner", "repo"))) == 1


def test_revert_pr_is_excluded():
    client = _client(_pr(title='Revert "Add empty check"'), [], [{"filename": "foo.py", "patch": BIG_PATCH}])
    assert list(mine_clean_hunks(client, "owner", "repo")) == []


def test_unmerged_pr_is_excluded():
    client = _client(_pr(merged=False), [], [{"filename": "foo.py", "patch": BIG_PATCH}])
    assert list(mine_clean_hunks(client, "owner", "repo")) == []


def test_tiny_patch_is_excluded():
    client = _client(_pr(), [], [{"filename": "foo.py", "patch": "@@ -1 +1 @@\n-a\n+b"}])
    assert list(mine_clean_hunks(client, "owner", "repo")) == []


def test_files_per_pr_caps_output():
    files = [
        {"filename": "a.py", "patch": BIG_PATCH},
        {"filename": "b.py", "patch": BIG_PATCH},
        {"filename": "c.py", "patch": BIG_PATCH},
    ]
    client = _client(_pr(), [], files)

    cases = list(mine_clean_hunks(client, "owner", "repo", files_per_pr=2))

    assert len(cases) == 2
