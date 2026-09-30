"""Cooperative machine renewal, requested only between Hermes tool rounds."""
import time


class RotationDeadline:
    def __init__(self, seconds, clock=time.monotonic):
        self.clock = clock
        self.deadline = clock() + seconds if seconds else None
        self.requested = False

    def step(self, agent):
        # Hermes invokes step_callback before the next request with no tools
        # in flight. Never interrupt a running tool to rotate the machine.
        if not self.requested and self.deadline is not None and self.clock() >= self.deadline:
            agent.interrupt()
            self.requested = True

    def can_continue(self, result):
        if not self.requested or not result.get('interrupted') or result.get('failed'):
            return False
        messages = result.get('messages')
        if not isinstance(messages, list) or not messages:
            return False
        pending = set()
        for message in messages:
            for call in message.get('tool_calls') or []:
                pending.add(call['id'])
            if message.get('role') == 'tool':
                pending.discard(message.get('tool_call_id'))
        return not pending
