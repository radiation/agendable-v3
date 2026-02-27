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
_key_windows: dict[str, int] = {}
_key_last_seen: dict[str, float] = {}
_lock = Lock()
_ops_since_sweep = 0

_STALE_MULTIPLIER = 3
_MIN_STALE_SECONDS = 60
_SWEEP_EVERY_OPS = 256


def _full_key(rule: RateLimitRule, key: str) -> str:
    return f"{rule.bucket}:{key}"


def _prune_history(history: deque[float], cutoff: float) -> None:
    while history and history[0] <= cutoff:
        history.popleft()


def _should_rate_limit(rule: RateLimitRule, key: str, now: float) -> bool:
    full_key = _full_key(rule, key)
    history = _attempts.setdefault(full_key, deque())
    _key_windows[full_key] = rule.window_seconds
    _key_last_seen[full_key] = now

    cutoff = now - rule.window_seconds
    _prune_history(history, cutoff)
    return len(history) >= rule.max_attempts


def _record_attempt(rule: RateLimitRule, key: str, now: float) -> None:
    full_key = _full_key(rule, key)
    history = _attempts.setdefault(full_key, deque())
    _key_windows[full_key] = rule.window_seconds
    _key_last_seen[full_key] = now
    history.append(now)


def _sweep_stale_keys(now: float) -> None:
    stale_keys: list[str] = []
    for full_key, history in _attempts.items():
        window_seconds = _key_windows.get(full_key, _MIN_STALE_SECONDS)
        cutoff = now - window_seconds
        _prune_history(history, cutoff)

        last_seen = _key_last_seen.get(full_key, 0.0)
        stale_age_seconds = max(window_seconds * _STALE_MULTIPLIER, _MIN_STALE_SECONDS)
        if not history and last_seen <= now - stale_age_seconds:
            stale_keys.append(full_key)

    for full_key in stale_keys:
        _attempts.pop(full_key, None)
        _key_windows.pop(full_key, None)
        _key_last_seen.pop(full_key, None)


def _maybe_sweep(now: float) -> None:
    global _ops_since_sweep
    _ops_since_sweep += 1
    if _ops_since_sweep < _SWEEP_EVERY_OPS:
        return
    _sweep_stale_keys(now)
    _ops_since_sweep = 0


def is_rate_limited(rule: RateLimitRule, key: str) -> bool:
    if rule.max_attempts < 1 or rule.window_seconds < 1:
        return False

    now = monotonic()
    with _lock:
        limited = _should_rate_limit(rule, key, now)
        _maybe_sweep(now)
        return limited


def consume_rate_limit(rule: RateLimitRule, key: str) -> bool:
    if rule.max_attempts < 1 or rule.window_seconds < 1:
        return False

    now = monotonic()

    with _lock:
        if _should_rate_limit(rule, key, now):
            _maybe_sweep(now)
            return True

        _record_attempt(rule, key, now)
        _maybe_sweep(now)
        return False


def reset_rate_limit_state() -> None:
    global _ops_since_sweep
    with _lock:
        _attempts.clear()
        _key_windows.clear()
        _key_last_seen.clear()
        _ops_since_sweep = 0
