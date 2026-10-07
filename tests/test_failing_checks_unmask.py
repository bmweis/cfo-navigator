"""#602: a raise inside run_all() must surface as itself, not as the
UnboundLocalError the old `finally` produced, and must release the sentinel."""
import pytest

from webapp import tasks


class Boom(RuntimeError):
    pass


def test_real_exception_surfaces_and_sentinel_released(monkeypatch):
    tasks._checks_cache = None
    tasks._checks_computing = False

    def raiser():
        raise Boom("real failure")

    monkeypatch.setattr(tasks, "_compute_failing_checks_count", raiser)
    with pytest.raises(Boom):
        tasks._failing_checks_count()
    assert tasks._checks_computing is False
    assert tasks._checks_cache is None  # a failure is never cached
    # next call is not stuck behind a leaked sentinel
    monkeypatch.setattr(tasks, "_compute_failing_checks_count", lambda: 3)
    assert tasks._failing_checks_count() == 3
