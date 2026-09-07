from reviewbench.cache import ResponseCache, cache_key


def test_cache_key_is_order_independent():
    a = cache_key(model="m", prompt="p", schema={"b": 1, "a": 2})
    b = cache_key(schema={"a": 2, "b": 1}, prompt="p", model="m")
    assert a == b


def test_cache_key_changes_with_input():
    a = cache_key(model="m", prompt="p")
    b = cache_key(model="m", prompt="q")
    assert a != b


def test_round_trip_and_stats(tmp_path):
    cache = ResponseCache(tmp_path)
    key = cache_key(prompt="hello")

    assert cache.get(key) is None
    cache.set(key, {"result": 42})
    assert cache.get(key) == {"result": 42}
    assert cache.stats == {"hits": 1, "misses": 1}


def test_disabled_cache_never_stores_or_returns(tmp_path):
    cache = ResponseCache(tmp_path, enabled=False)
    key = cache_key(prompt="hello")

    cache.set(key, {"result": 42})
    assert cache.get(key) is None
    assert cache.stats == {"hits": 0, "misses": 0}


def test_corrupt_entry_degrades_to_miss(tmp_path):
    cache = ResponseCache(tmp_path)
    key = cache_key(prompt="hello")
    cache.set(key, {"result": 1})

    path = cache._path_for(key)
    path.write_text("{not valid json", encoding="utf-8")

    assert cache.get(key) is None
    assert cache.stats["misses"] == 1


def test_entries_are_sharded_by_key_prefix(tmp_path):
    cache = ResponseCache(tmp_path)
    key = cache_key(prompt="hello")
    cache.set(key, {"x": 1})
    assert (tmp_path / key[:2] / f"{key}.json").exists()
