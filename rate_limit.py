"""Tiny in-memory per-user cooldown (task 096).

Not a security boundary by itself: it only stops a single user from hammering the bot (every callback/command
triggers catalogue refreshes and web calls). State is process-local and bounded, so it can never grow without limit.
"""
import time
from collections import OrderedDict


class Cooldown:
    def __init__(self, interval: float = 1.0, max_users: int = 5000, clock=time.monotonic):
        if interval <= 0 or max_users <= 0:
            raise ValueError("interval and max_users must be positive")
        self.interval = float(interval)
        self.max_users = int(max_users)
        self._clock = clock
        self._last: "OrderedDict[object, float]" = OrderedDict()

    def allow(self, key) -> bool:
        """True (and the action is recorded) unless `key` already acted less than `interval` seconds ago.

        A rejected attempt does not restart the timer, so a user who keeps pressing is served again one interval
        after their last *accepted* action instead of being locked out for as long as they spam."""
        now = self._clock()
        last = self._last.get(key)
        if last is not None and 0 <= now - last < self.interval:
            return False
        self._last[key] = now
        self._last.move_to_end(key)
        while len(self._last) > self.max_users:
            self._last.popitem(last=False)
        return True

    def __len__(self) -> int:
        return len(self._last)
