from contest.ratelimit import RateLimiter


def test_allows_up_to_the_limit_then_reports_the_wait_and_recovers():
    t = {"now": 100.0}
    rl = RateLimiter({"place": (3, 60), "read": (5, 60)}, lambda: t["now"])
    assert [rl.check("u", "place") for _ in range(3)] == [0.0, 0.0, 0.0]
    wait = rl.check("u", "place")
    assert 59.0 < wait <= 60.0
    assert rl.check("u", "place") > 0                       # a refused call is not recorded: still blocked, same window
    t["now"] = 160.5
    assert rl.check("u", "place") == 0.0


def test_users_and_buckets_are_independent_and_an_unknown_bucket_uses_read():
    rl = RateLimiter({"place": (1, 60), "read": (2, 60)}, lambda: 0.0)
    assert rl.check("a", "place") == 0.0 and rl.check("a", "place") > 0
    assert rl.check("b", "place") == 0.0
    assert rl.check("a", "mystery") == 0.0 and rl.check("a", "mystery") == 0.0 and rl.check("a", "mystery") > 0
