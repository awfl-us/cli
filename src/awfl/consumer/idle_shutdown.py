"""Idle shutdown accounting shared by project SSE and MCP work."""
import time


class IdleShutdown:
    def __init__(self, timeout_seconds, *, is_busy, clock=time.monotonic):
        self.timeout_seconds = float(timeout_seconds)
        self.is_busy = is_busy
        self.clock = clock
        self.last_activity = clock()

    def touch(self):
        self.last_activity = self.clock()

    def is_due(self):
        if self.is_busy():
            self.touch()
            return False
        return self.timeout_seconds > 0 and self.clock() - self.last_activity >= self.timeout_seconds
