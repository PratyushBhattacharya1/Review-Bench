import pytest
import requests
import responses

from reviewbench.github_client import MAX_TRANSIENT_RETRIES, GitHubClient, GitHubError


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


@responses.activate
def test_transient_connection_error_is_retried():
    # A long scan is thousands of requests; one dropped connection must not
    # end the run.
    responses.add(
        responses.GET,
        "https://api.github.com/repos/o/r/pulls",
        body=requests.ConnectionError("Remote end closed connection without response"),
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


@responses.activate
def test_connection_errors_eventually_give_up():
    for _ in range(MAX_TRANSIENT_RETRIES + 2):
        responses.add(
            responses.GET,
            "https://api.github.com/repos/o/r/pulls",
            body=requests.ConnectionError("boom"),
        )

    client = GitHubClient(token="t", sleep_fn=lambda _: None)
    with pytest.raises(GitHubError, match="network failures"):
        list(client.get_paginated("/repos/o/r/pulls"))


@responses.activate
def test_server_error_is_retried_then_succeeds():
    responses.add(responses.GET, "https://api.github.com/repos/o/r/pulls", json={}, status=502)
    responses.add(
        responses.GET, "https://api.github.com/repos/o/r/pulls", json=[{"number": 7}], status=200
    )

    sleeps: list[float] = []
    client = GitHubClient(token="t", sleep_fn=sleeps.append)
    items = list(client.get_paginated("/repos/o/r/pulls"))

    assert [i["number"] for i in items] == [7]
    assert len(sleeps) == 1


@responses.activate
def test_search_issues_paginates_and_respects_max_results():
    responses.add(
        responses.GET,
        "https://api.github.com/search/issues",
        json={"total_count": 3, "items": [{"number": 1}, {"number": 2}, {"number": 3}]},
        status=200,
    )

    client = GitHubClient(token="t")
    items = list(client.search_issues("repo:o/r revert in:title", per_page=3, max_results=2))

    assert [i["number"] for i in items] == [1, 2]
