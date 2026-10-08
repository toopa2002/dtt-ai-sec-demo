"""Latency and error metrics, logged per turn (research R14; SC-003, SC-003a).

Metrics go to the JSON log stream (`metric`, `value_ms`), which is enough to compute p95s with any log tool; an
in-memory summary is exposed on /healthz/metrics for a quick look.
"""

import logging
from collections import defaultdict, deque

log = logging.getLogger("onboarding.metrics")
_window: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=500))
_counters: dict[str, int] = defaultdict(int)


def record_metric(name: str, value_ms: float, **extra: str) -> None:
    _window[name].append(value_ms)
    log.info("metric", extra={"metric": name, "value_ms": round(value_ms, 1), **extra})


def count(name: str) -> None:
    _counters[name] += 1


def p95(name: str) -> float | None:
    values = sorted(_window.get(name, ()))
    if not values:
        return None
    return values[min(len(values) - 1, int(round(0.95 * (len(values) - 1))))]


def summary() -> dict:
    return {
        "p95_ms": {k: p95(k) for k in _window},
        "samples": {k: len(v) for k, v in _window.items()},
        "counters": dict(_counters),
    }
