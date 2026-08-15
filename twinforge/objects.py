"""Industrial objects.

These are the primitives a factory is built from. Every one is a
:class:`~twinforge.core.TwinObject`, so they compose into a tree and talk to
each other through signals.

Material flow is **pull-with-blocking**, which is how real lines behave:

* a station pulls work from upstream when it is free,
* it blocks when the downstream buffer is full,
* it starves when the upstream buffer is empty.

Those two states are where bottlenecks reveal themselves, so they are tracked
as first-class time, not inferred afterwards.
"""

from __future__ import annotations

from collections import deque
from typing import Any

from .core import TwinObject
from .sim import Part, duration

# Station states
IDLE, RUNNING, BLOCKED, STARVED, DOWN, SETUP = (
    "idle", "running", "blocked", "starved", "down", "setup",
)


class StateTracker:
    """Accumulates time-in-state so utilisation is measured, not estimated."""

    def __init__(self, initial: str = IDLE) -> None:
        self.state = initial
        self.since = 0.0
        self.totals: dict[str, float] = {}

    def to(self, state: str, now: float) -> None:
        if state == self.state:
            return
        self.totals[self.state] = self.totals.get(self.state, 0.0) + (now - self.since)
        self.state = state
        self.since = now

    def close(self, now: float) -> None:
        self.totals[self.state] = self.totals.get(self.state, 0.0) + (now - self.since)
        self.since = now

    def fraction(self, state: str, total: float) -> float:
        if total <= 0:
            return 0.0
        return self.totals.get(state, 0.0) / total


class Buffer(TwinObject):
    """Bounded store between stations. Capacity is what creates blocking."""

    type_name = "Buffer"

    def __init__(self, name: str | None = None, capacity: int = 10, **props: Any) -> None:
        super().__init__(name, capacity=capacity, **props)
        self.capacity = int(capacity)
        self.items: deque[Part] = deque()
        self.signal("received")
        self.signal("released")
        self.signal("full")
        self.signal("empty")
        self._level_time = 0.0   # integral of level over time -> average WIP
        self._last_change = 0.0
        self.peak = 0

    def reset(self) -> None:
        self.items.clear()
        self._level_time = 0.0
        self._last_change = 0.0
        self.peak = 0

    def _integrate(self, now: float) -> None:
        self._level_time += len(self.items) * (now - self._last_change)
        self._last_change = now

    @property
    def is_full(self) -> bool:
        return len(self.items) >= self.capacity

    @property
    def is_empty(self) -> bool:
        return not self.items

    def push(self, part: Part, now: float) -> bool:
        """Insert a part. Returns False when full — the caller must block."""
        if self.is_full:
            return False
        self._integrate(now)
        self.items.append(part)
        self.peak = max(self.peak, len(self.items))
        part.touch(now, self.path, "queued")
        self.emit("received", part)
        if self.is_full:
            self.emit("full")
        return True

    def pop(self, now: float) -> Part | None:
        if not self.items:
            return None
        self._integrate(now)
        part = self.items.popleft()
        self.emit("released", part)
        if self.is_empty:
            self.emit("empty")
        return part

    def finalise(self, now: float) -> None:
        self._integrate(now)

    def average_level(self, total_time: float) -> float:
        return self._level_time / total_time if total_time > 0 else 0.0


class Station(TwinObject):
    """Base for anything that pulls a part, holds it, and passes it on."""

    type_name = "Station"

    def __init__(self, name: str | None = None, **props: Any) -> None:
        super().__init__(name, **props)
        self.inputs: list[Buffer] = []
        self.outputs: list[Buffer] = []
        self.tracker = StateTracker()
        self.processed = 0
        self.current: Part | None = None
        self._pending: Part | None = None
        self._wake_scheduled = False
        for s in ("cycle_started", "cycle_completed", "blocked", "starved", "failed", "repaired"):
            self.signal(s)

    def feeds(self, buffer: Buffer) -> Station:
        self.outputs.append(buffer)
        # subscribe once, here — not on every block, which would stack handlers
        buffer.signal("released").connect(self._nudge)
        return self

    def fed_by(self, buffer: Buffer) -> Station:
        self.inputs.append(buffer)
        buffer.signal("received").connect(self._nudge)
        return self

    # -- flow ------------------------------------------------------------
    #
    # Signals never do work directly. They only mark the station as needing
    # attention and queue a zero-delay wake-up. Without this, a push cascades
    # into a pull, which cascades into another push, and a long line recurses
    # until the stack gives out. Routing through the event queue turns that
    # chain into iteration and keeps ordering deterministic.

    def _nudge(self, *_: Any) -> None:
        if self._wake_scheduled or self.engine is None:
            return
        self._wake_scheduled = True
        self.engine.schedule(0.0, self._wake)

    def _wake(self) -> None:
        self._wake_scheduled = False
        if self._pending is not None:
            self._try_deliver(self._pending)
        else:
            self._try_start()

    def _take_input(self) -> Part | None:
        for buf in self.inputs:
            part = buf.pop(self.engine.now)
            if part is not None:
                return part
        return None

    def _try_deliver(self, part: Part) -> bool:
        now = self.engine.now
        for buf in self.outputs:
            if buf.push(part, now):
                self._pending = None
                self.current = None
                self.processed += 1
                self.emit("cycle_completed", part)
                self.tracker.to(IDLE, now)
                self._nudge()          # look for the next part on the queue
                return True
        # nowhere to put it: block until a downstream release nudges us
        self._pending = part
        if self.tracker.state != BLOCKED:
            self.tracker.to(BLOCKED, now)
            self.emit("blocked", part)
        return False

    def _try_start(self) -> None:
        raise NotImplementedError

    def finalise(self, now: float) -> None:
        self.tracker.close(now)

    def utilisation(self, total: float) -> float:
        return self.tracker.fraction(RUNNING, total)


class Machine(Station):
    """A processing station with a cycle time and optional reliability.

    ``mtbf``/``mttr`` make it fail and recover; leave them out for a perfect
    machine. Failures are sampled from the run's seeded RNG, so a scenario
    replays identically.
    """

    type_name = "Machine"

    def __init__(
        self,
        name: str | None = None,
        cycle_time: Any = "30s",
        mtbf: Any = None,
        mttr: Any = None,
        **props: Any,
    ) -> None:
        super().__init__(name, cycle_time=cycle_time, mtbf=mtbf, mttr=mttr, **props)
        self.cycle_time = cycle_time
        self.mtbf = mtbf
        self.mttr = mttr
        self.failures = 0
        self.down_time = 0.0
        self._broken = False

    def setup(self) -> None:
        self.tracker.to(STARVED, self.engine.now)
        if self.mtbf is not None:
            self._schedule_failure()
        self._try_start()

    def _schedule_failure(self) -> None:
        spec = self.mtbf if isinstance(self.mtbf, dict) else {"exponential": {"mean": self.mtbf}}
        self.engine.schedule(self.engine.sample(spec), self._fail)

    def _fail(self) -> None:
        if self._broken:
            return
        self._broken = True
        self.failures += 1
        now = self.engine.now
        self._resume_state = self.tracker.state
        self.tracker.to(DOWN, now)
        self.emit("failed", self)
        self.engine.record(self.path, "failed")
        repair = self.engine.sample(
            self.mttr if isinstance(self.mttr, dict) else {"constant": {"value": self.mttr or "10min"}}
        )
        self.down_time += repair
        self.engine.schedule(repair, self._repair)

    def _repair(self) -> None:
        self._broken = False
        self.tracker.to(IDLE, self.engine.now)
        self.emit("repaired", self)
        self.engine.record(self.path, "repaired")
        if self.mtbf is not None:
            self._schedule_failure()
        self._nudge()

    def _try_start(self) -> None:
        if self._broken or self.current is not None or self._pending is not None:
            return
        now = self.engine.now
        part = self._take_input()
        if part is None:
            if self.tracker.state not in (STARVED, DOWN):
                self.tracker.to(STARVED, now)
                self.emit("starved", self)
            return
        self.current = part
        self.tracker.to(RUNNING, now)
        part.touch(now, self.path, "processing")
        self.emit("cycle_started", part)
        self.engine.schedule(self.engine.sample(self.cycle_time), self._finish)

    def _finish(self) -> None:
        if self._broken:
            # failed mid-cycle: the part waits for repair
            self.engine.schedule(1.0, self._finish)
            return
        part = self.current
        if part is None:
            return
        self._try_deliver(part)


class Source(TwinObject):
    """Introduces material into the twin at an interval."""

    type_name = "Source"

    def __init__(
        self,
        name: str | None = None,
        interval: Any = "30s",
        part_kind: str = "part",
        limit: int | None = None,
        **props: Any,
    ) -> None:
        super().__init__(name, interval=interval, part_kind=part_kind, limit=limit, **props)
        self.interval = interval
        self.part_kind = part_kind
        self.limit = limit
        self.outputs: list[Buffer] = []
        self.created = 0
        self.rejected = 0
        self.signal("created")

    def feeds(self, buffer: Buffer) -> Source:
        self.outputs.append(buffer)
        return self

    def setup(self) -> None:
        self.engine.schedule(self.engine.sample(self.interval), self._generate)

    def _generate(self) -> None:
        if self.limit is not None and self.created >= self.limit:
            return
        now = self.engine.now
        part = Part(self.part_kind, now)
        placed = any(buf.push(part, now) for buf in self.outputs)
        if placed:
            self.created += 1
            part.touch(now, self.path, "created")
            self.emit("created", part)
        else:
            self.rejected += 1  # line is backed up to the door
        self.engine.schedule(self.engine.sample(self.interval), self._generate)


class Sink(TwinObject):
    """Collects finished material and records lead time."""

    type_name = "Sink"

    def __init__(self, name: str | None = None, **props: Any) -> None:
        super().__init__(name, **props)
        self.inputs: list[Buffer] = []
        self.completed: list[Part] = []
        self.signal("completed")

    def fed_by(self, buffer: Buffer) -> Sink:
        self.inputs.append(buffer)
        buffer.signal("received").connect(self._drain)
        return self

    def _drain(self, part: Part) -> None:
        now = self.engine.now
        for buf in self.inputs:
            while True:
                got = buf.pop(now)
                if got is None:
                    break
                got.completed_at = now
                got.touch(now, self.path, "completed")
                self.completed.append(got)
                self.emit("completed", got)

    @property
    def throughput(self) -> int:
        return len(self.completed)

    def lead_times(self) -> list[float]:
        return [p.lead_time for p in self.completed if p.lead_time is not None]


class Conveyor(Station):
    """Transport with a fixed travel time. Models distance, not processing."""

    type_name = "Conveyor"

    def __init__(self, name: str | None = None, travel_time: Any = "10s", **props: Any) -> None:
        super().__init__(name, travel_time=travel_time, **props)
        self.travel_time = travel_time

    def setup(self) -> None:
        self.tracker.to(STARVED, self.engine.now)
        self._try_start()

    def _try_start(self) -> None:
        if self.current is not None or self._pending is not None:
            return
        now = self.engine.now
        part = self._take_input()
        if part is None:
            if self.tracker.state != STARVED:
                self.tracker.to(STARVED, now)
            return
        self.current = part
        self.tracker.to(RUNNING, now)
        self.engine.schedule(duration(self.travel_time), self._arrive)

    def _arrive(self) -> None:
        if self.current is not None:
            self._try_deliver(self.current)


class Worker(TwinObject):
    """A named operator. Attach to stations to model manning and shifts."""

    type_name = "Worker"

    def __init__(self, name: str | None = None, role: str = "operator", **props: Any) -> None:
        super().__init__(name, role=role, **props)
        self.role = role


class Area(TwinObject):
    """Pure grouping: a line, cell, warehouse or zone. No behaviour."""

    type_name = "Area"


class Factory(TwinObject):
    """Root of a twin."""

    type_name = "Factory"


#: Types the ``.twin`` loader knows about. Plugins extend this.
REGISTRY: dict[str, type[TwinObject]] = {
    cls.type_name: cls
    for cls in (Factory, Area, Buffer, Machine, Source, Sink, Conveyor, Worker)
}
