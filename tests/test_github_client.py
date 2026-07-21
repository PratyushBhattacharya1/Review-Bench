import responses

from reviewbench.github_client import GitHubClient


@responses.activate
def test_get_paginated_follows_link_header():
    responses.add(
        responses.GET,
        "https://api.github.com/repos/o/r/pulls",
        json=[{"number": 1}],
        headers={"Link": '<https://api.github.com/repos/o/r/pulls?page=2>; rel="next"'},
        status=200,
    )
    responses.add(
        responses.GET,
        "https://api.github.com/repos/o/r/pulls?page=2",
        json=[{"number": 2}],
        status=200,
    )

    client = GitHubClient(token="t")
    items = list(client.get_paginated("/repos/o/r/pulls"))

    assert [i["number"] for i in items] == [1, 2]


@responses.activate
def test_get_paginated_stops_without_link_header():
    responses.add(
        responses.GET,
        "https://api.github.com/repos/o/r/pulls",
        json=[{"number": 1}],
        status=200,
    )

    client = GitHubClient(token="t")
    items = list(client.get_paginated("/repos/o/r/pulls"))

    assert [i["number"] for i in items] == [1]


@responses.activate
def test_rate_limit_triggers_backoff_then_retries():
    responses.add(
        responses.GET,
        "https://api.github.com/repos/o/r/pulls",
        json={"message": "API rate limit exceeded"},
        headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "9999999999"},
        status=403,
    )
    responses.add(
        responses.GET,
        "https://api.github.com/repos/o/r/pulls",
        json=[{"number": 1}],
        status=200,
    )

    sleeps: list[float] = []
    client = GitHubClient(token="t", sleep_fn=sleeps.append)
    items = list(client.get_paginated("/repos/o/r/pulls"))

    assert [i["number"] for i in items] == [1]
    assert len(sleeps) == 1
    assert sleeps[0] > 0


@responses.activate
def test_get_single_resource():
    responses.add(
        responses.GET,
        "https://api.github.com/repos/o/r/commits/abc/pulls",
        json=[{"number": 42, "merged_at": "2024-01-01T00:00:00Z"}],
        status=200,
    )

    client = GitHubClient(token="t")
    result = client.get("/repos/o/r/commits/abc/pulls")

    assert result == [{"number": 42, "merged_at": "2024-01-01T00:00:00Z"}]
