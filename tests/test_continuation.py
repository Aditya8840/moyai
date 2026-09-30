from types import SimpleNamespace

import pytest

from app.config import Settings
from sandbox.continuation import RotationDeadline


def test_rotation_requests_stop_only_at_next_safe_step_and_checks_tool_results():
    now = [0]
    stops = []
    deadline = RotationDeadline(10, clock=lambda: now[0])
    agent = SimpleNamespace(interrupt=lambda: stops.append('stop'))
    deadline.step(agent)
    now[0] = 11
    assert not stops  # No timer interrupts in-flight work.
    deadline.step(agent)
    deadline.step(agent)
    assert stops == ['stop']
    history = [{'role':'assistant', 'tool_calls':[{'id':'call1'}]}]
    result = {'interrupted': True, 'messages': history}
    assert not deadline.can_continue(result)
    history.append({'role':'tool', 'tool_call_id':'call1', 'content':'Already done'})
    assert deadline.can_continue(result)
    assert not deadline.can_continue({**result, 'failed': True})
    assert not deadline.can_continue({**result, 'interrupted': False, 'completed': True})
    assert not deadline.can_continue({'interrupted':True})


def test_disabled_rotation_and_explicit_duration_limit():
    deadline = RotationDeadline(0)
    deadline.step(SimpleNamespace(interrupt=lambda: pytest.fail('Unexpected interrupt')))
    settings = Settings(_env_file=None, run_timeout_seconds=1800)
    assert settings.sandbox_lifetime_seconds() == 2040
    assert Settings(_env_file=None, run_timeout_seconds=0).sandbox_lifetime_seconds() == 86400
    with pytest.raises(ValueError):
        Settings(_env_file=None, run_timeout_seconds=10)
