from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from threading import Lock
from time import monotonic


@dataclass(frozen=True)
class RateLimitRule:
    bucket: str
    max_attempts: int
    window_seconds: int


_attempts: dict[str, deque[float]] = {}
_lock = Lock()


def _full_key(rule: RateLimitRule, key: str) -> str:
    return f"{rule.bucket}:{key}"


def consume_rate_limit(rule: RateLimitRule, key: str) -> bool:
    if rule.max_attempts < 1 or rule.window_seconds < 1:
        return False

    now = monotonic()
    cutoff = now - rule.window_seconds
    full_key = _full_key(rule, key)

    with _lock:
        history = _attempts.setdefault(full_key, deque())
        while history and history[0] <= cutoff:
            history.popleft()

        if len(history) >= rule.max_attempts:
            return True

        history.append(now)
        return False


def reset_rate_limit_state() -> None:
    with _lock:
        _attempts.clear()
