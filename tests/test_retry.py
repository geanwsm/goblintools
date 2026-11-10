import pytest

from goblintools import retry
from goblintools.retry import retry_until_deadline


class Transient(Exception):
    pass


class Clock:
    def __init__(self):
        self.now = 0.0
        self.slept = []

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds


def _flaky(failures, result="ok", exc=Transient):
    calls = {"n": 0}

    def func():
        calls["n"] += 1
        if calls["n"] <= failures:
            raise exc("boom")
        return result

    return func, calls


def _run(func, clock, max_wait_s=30.0, rng=lambda: 1.0):
    return retry_until_deadline(
        func, max_wait_s=max_wait_s, base_delay=0.5, max_delay=8.0,
        exceptions=(Transient,), sleep=clock.sleep, clock=clock.time, rng=rng,
    )


def test_returns_after_transient_failures():
    clock = Clock()
    func, calls = _flaky(failures=2)
    assert _run(func, clock) == "ok"
    assert calls["n"] == 3
    assert clock.slept == [0.5, 1.0]


def test_delay_is_capped_and_scaled_by_jitter():
    clock = Clock()
    func, _ = _flaky(failures=6)
    _run(func, clock, max_wait_s=100.0, rng=lambda: 0.25)
    assert clock.slept == [0.125, 0.25, 0.5, 1.0, 2.0, 2.0]


def test_raises_last_error_when_budget_runs_out():
    clock = Clock()
    func, calls = _flaky(failures=1000)
    with pytest.raises(Transient):
        _run(func, clock, max_wait_s=10.0)
    assert sum(clock.slept) <= 10.0
    assert calls["n"] == len(clock.slept) + 1


def test_never_sleeps_past_the_budget():
    clock = Clock()
    func, _ = _flaky(failures=1000)
    with pytest.raises(Transient):
        _run(func, clock, max_wait_s=4.0)
    assert clock.slept == [0.5, 1.0, 2.0]


def test_other_errors_are_not_retried():
    clock = Clock()
    func, calls = _flaky(failures=5, exc=ValueError)
    with pytest.raises(ValueError):
        _run(func, clock)
    assert calls["n"] == 1
    assert clock.slept == []


@pytest.mark.parametrize("budget", [0, -5])
def test_zero_or_negative_budget_means_single_attempt(budget):
    clock = Clock()
    func, calls = _flaky(failures=5)
    with pytest.raises(Transient):
        _run(func, clock, max_wait_s=budget)
    assert calls["n"] == 1
    assert clock.slept == []


def test_defaults_use_module_time_and_random(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(retry.time, "sleep", clock.sleep)
    monkeypatch.setattr(retry.time, "monotonic", clock.time)
    monkeypatch.setattr(retry.random, "random", lambda: 1.0)
    func, _ = _flaky(failures=1)
    assert retry_until_deadline(func, max_wait_s=5.0, base_delay=0.5, max_delay=8.0, exceptions=(Transient,)) == "ok"
    assert clock.slept == [0.5]
