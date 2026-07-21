from reviewbench.models import Case, make_case_id, read_jsonl, write_jsonl


def _case(id_="a", **overrides) -> Case:
    fields = dict(
        id=id_,
        repo="owner/repo",
        pr_number=1,
        provenance="fixup",
        label="defect",
        file_path="foo.py",
        diff_hunk="@@ -1 +1 @@\n-old\n+new",
        base_commit_sha="base",
        head_commit_sha="head",
    )
    fields.update(overrides)
    return Case(**fields)


def test_json_round_trip():
    case = _case(defect_category="null-deref", source_urls=["https://example.com"])
    restored = Case.from_json(case.to_json())
    assert restored == case


def test_make_case_id_stable_and_sensitive_to_input():
    a = make_case_id("fixup", "owner/repo", "1", "foo.py")
    b = make_case_id("fixup", "owner/repo", "1", "foo.py")
    c = make_case_id("fixup", "owner/repo", "2", "foo.py")
    assert a == b
    assert a != c


def test_write_jsonl_dedups_by_id(tmp_path):
    path = tmp_path / "out.jsonl"
    cases = [_case(id_="dup"), _case(id_="dup"), _case(id_="unique")]

    written = write_jsonl(cases, path)

    assert written == 2
    restored = list(read_jsonl(path))
    assert {c.id for c in restored} == {"dup", "unique"}


def test_write_jsonl_creates_parent_dirs(tmp_path):
    path = tmp_path / "nested" / "out.jsonl"
    write_jsonl([_case()], path)
    assert path.exists()
