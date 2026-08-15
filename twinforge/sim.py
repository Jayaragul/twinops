"""Discrete-event simulation engine.

Time advances event to event, never tick to tick — an eight-hour shift with a
handful of machines costs a few thousand events, not 28,800 idle steps. That is
what makes running thousands of scenarios cheap.
"""

from __future__ import annotations

import heapq
import itertools
import random
from typing import Any, Callable

# Time is expressed in seconds throughout the engine.
SECOND, MINUTE, HOUR, DAY = 1.0, 60.0, 3600.0, 86400.0

_DURATION_UNITS = {
    "s": SECOND, "sec": SECOND, "secs": SECOND, "second": SECOND, "seconds": SECOND,
    "m": MINUTE, "min": MINUTE, "mins": MINUTE, "minute": MINUTE, "minutes": MINUTE,
    "h": HOUR, "hr": HOUR, "hrs": HOUR, "hour": HOUR, "hours": HOUR,
    "d": DAY, "day": DAY, "days": DAY,
}


def duration(value: Any) -> float:
    """Parse a duration into seconds.

    Accepts numbers (already seconds) or strings such as ``42s``, ``25min``,
    ``8h``. Written so ``.twin`` files can stay readable.
    """
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        raise TypeError(f"cannot read a duration from {value!r}")
    text = value.strip().lower()
    for i, ch in enumerate(text):
        if not (ch.isdigit() or ch in ".-+"):
            number, unit = text[:i], text[i:].strip()
            break
    else:
        number, unit = text, "s"
    if not number:
        raise ValueError(f"no number in duration {value!r}")
    factor = _DURATION_UNITS.get(unit)
    if factor is None:
        raise ValueError(f"unknown time unit {unit!r} in {value!r}")
    return float(number) * factor


def fmt_time(seconds: float) -> str:
    """Format simulation seconds as ``D d HH:MM:SS`` for logs and reports."""
    seconds = max(0.0, float(seconds))
    days, rem = divmod(int(seconds), 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    stamp = f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{days}d {stamp}" if days else stamp


class Part:
    """A unit of material moving through the twin."""

    __slots__ = ("id", "kind", "created_at", "completed_at", "history")

    _seq = itertools.count(1)

    def __init__(self, kind: str = "part", created_at: float = 0.0) -> None:
        self.id = next(Part._seq)
        self.kind = kind
        self.created_at = created_at
        self.completed_at: float | None = None
        self.history: list[tuple[float, str, str]] = []

    def touch(self, when: float, where: str, what: str) -> None:
        self.history.append((when, where, what))

    @property
    def lead_time(self) -> float | None:
        if self.completed_at is None:
            return None
        return self.completed_at - self.created_at

    def __repr__(self) -> str:
        return f"<Part #{self.id} {self.kind}>"


class SimulationEngine:
    """Event queue, clock, and run loop.

    Events are ordered by ``(time, sequence)`` so simultaneous events fire in
    the order they were scheduled — the usual convention, and it keeps runs
    reproducible.
    """

    def __init__(self, root=None, seed: int | None = None) -> None:
        self.now: float = 0.0
        self.root = root
        self.random = random.Random(seed)
        self._queue: list[tuple[float, int, Callable[[], Any]]] = []
        self._seq = itertools.count()
        self._running = False
        self.events_processed = 0
        self.log: list[tuple[float, str, str]] = []
        self.log_enabled = False
        if root is not None:
            root.bind(self)

    # ------------------------------------------------------------ scheduling

    def schedule(self, delay: float, callback: Callable[[], Any]) -> None:
        """Run ``callback`` after ``delay`` seconds of simulated time."""
        if delay < 0:
            raise ValueError("cannot schedule into the past")
        heapq.heappush(self._queue, (self.now + delay, next(self._seq), callback))

    def schedule_at(self, when: float, callback: Callable[[], Any]) -> None:
        if when < self.now:
            raise ValueError("cannot schedule into the past")
        heapq.heappush(self._queue, (when, next(self._seq), callback))

    # ------------------------------------------------------------------ run

    def run(self, until: float, progress: Callable[[float], None] | None = None) -> SimulationEngine:
        """Advance the clock until ``until`` seconds, or until events run out."""
        self._running = True
        next_report = 0.0
        while self._queue and self._running:
            when, _, callback = self._queue[0]
            if when > until:
                break
            heapq.heappop(self._queue)
            self.now = when
            callback()
            self.events_processed += 1
            if progress is not None and when >= next_report:
                progress(when)
                next_report = when + (until / 100.0 if until else 1.0)
        self.now = min(until, max(self.now, 0.0)) if not self._queue else self.now
        self.now = max(self.now, 0.0)
        if until > self.now:
            self.now = until  # let idle time count toward utilisation windows
        self._running = False
        for obj in (self.root.walk() if self.root else []):
            finalise = getattr(obj, "finalise", None)
            if callable(finalise):
                finalise(self.now)
        return self

    def stop(self) -> None:
        self._running = False

    # ------------------------------------------------------------- logging

    def record(self, source: str, message: str) -> None:
        if self.log_enabled:
            self.log.append((self.now, source, message))

    # --------------------------------------------------------- distributions

    def sample(self, spec: Any) -> float:
        """Sample a duration from a constant or a distribution spec.

        Accepts ``42``, ``"42s"``, or a mapping such as::

            {"normal": {"mean": "25min", "sd": "5min"}}
            {"exponential": {"mean": "480min"}}
            {"uniform": {"low": "10s", "high": "20s"}}
            {"triangular": {"low": "8s", "mode": "10s", "high": "18s"}}
        """
        if not isinstance(spec, dict):
            return duration(spec)
        if len(spec) != 1:
            raise ValueError(f"a distribution needs exactly one kind, got {list(spec)}")
        kind, params = next(iter(spec.items()))
        kind = kind.lower()
        if kind in ("constant", "fixed"):
            return duration(params if not isinstance(params, dict) else params["value"])
        if kind in ("normal", "gauss"):
            value = self.random.gauss(duration(params["mean"]), duration(params["sd"]))
            return max(0.0, value)
        if kind in ("exponential", "exp"):
            return self.random.expovariate(1.0 / duration(params["mean"]))
        if kind == "uniform":
            return self.random.uniform(duration(params["low"]), duration(params["high"]))
        if kind == "triangular":
            return self.random.triangular(
                duration(params["low"]), duration(params["high"]), duration(params["mode"])
            )
        raise ValueError(f"unknown distribution {kind!r}")
