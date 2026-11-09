"""Bounded in-process fixed-window limits; no external service is required."""
from __future__ import annotations
from collections import defaultdict, deque
import os
import threading
import time


class LocalRateLimiter:
    MAX_IDENTITIES = 10_000
    MAX_EVENTS = 100_000

    def __init__(self):
        self.policies = {
            "register": (self._setting("REGISTER", 5), self._setting("REGISTER_WINDOW_SECONDS", 3600)),
            "login": (self._setting("LOGIN", 10), self._setting("LOGIN_WINDOW_SECONDS", 300)),
            "analysis": (self._setting("ANALYSIS", 30), self._setting("ANALYSIS_WINDOW_SECONDS", 60)),
            "export": (self._setting("EXPORT", 15), self._setting("EXPORT_WINDOW_SECONDS", 60)),
        }
        self.events: dict[tuple[str, str], deque[float]] = defaultdict(deque)
        self.lock = threading.Lock()
        self.event_count = 0

    @staticmethod
    def _setting(suffix: str, default: int) -> int:
        value = int(os.getenv(f"QUERYLENS_RATE_{suffix}", str(default)))
        maximum = 604_800 if suffix.endswith("WINDOW_SECONDS") else 10_000
        if value < 1 or value > maximum:
            raise RuntimeError(f"QUERYLENS_RATE_{suffix} must be between 1 and {maximum}.")
        return value

    def consume(self, scope: str, identity: str, now: float | None = None) -> tuple[bool, int]:
        limit, window = self.policies[scope]
        current = time.monotonic() if now is None else now
        key = (scope, identity)
        with self.lock:
            if key not in self.events and len(self.events) >= self.MAX_IDENTITIES:
                self._purge(current)
                if len(self.events) >= self.MAX_IDENTITIES:
                    return False, window
            if self.event_count >= self.MAX_EVENTS:
                self._purge(current)
                if self.event_count >= self.MAX_EVENTS:
                    return False, window
            events = self.events[key]
            cutoff = current - window
            while events and events[0] <= cutoff:
                events.popleft()
                self.event_count -= 1
            if len(events) >= limit:
                return False, max(1, int(window - (current - events[0]) + 0.999))
            events.append(current)
            self.event_count += 1
            return True, 0

    def _purge(self, current: float) -> None:
        expired=[]
        for key,events in self.events.items():
            window=self.policies[key[0]][1]
            cutoff=current-window
            while events and events[0]<=cutoff:
                events.popleft(); self.event_count-=1
            if not events: expired.append(key)
        for key in expired: self.events.pop(key,None)
