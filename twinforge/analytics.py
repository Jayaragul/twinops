"""KPIs and bottleneck analysis.

The point of a twin is the answer, not the animation. This module turns a
finished run into the numbers an industrial engineer actually argues about.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .objects import BLOCKED, DOWN, RUNNING, STARVED, Buffer, Sink, Source, Station
from .sim import HOUR, fmt_time

# Report glyphs. Switched to ASCII on consoles that cannot encode box drawing.
_G = {"heavy": "═", "light": "─", "mark": " ◀", "arrow": "←"}


def set_ascii(ascii_only: bool) -> None:
    """Use ASCII-safe glyphs (legacy Windows consoles)."""
    if ascii_only:
        _G.update(heavy="=", light="-", mark=" <", arrow="<-")
    else:
        _G.update(heavy="═", light="─", mark=" ◀", arrow="←")


@dataclass
class StationReport:
    name: str
    processed: int
    utilisation: float
    blocked: float
    starved: float
    down: float
    failures: int

    @property
    def bottleneck_score(self) -> float:
        """How strongly this station constrains the line.

        A true bottleneck is busy while its neighbours wait: high utilisation
        and low starvation. Blocking counts too — a station blocked by the one
        after it is a symptom, and the score surfaces both for inspection.
        """
        return self.utilisation + self.blocked * 0.5 - self.starved * 0.5


@dataclass
class Report:
    duration: float
    events: int
    throughput: int = 0
    created: int = 0
    rejected: int = 0
    stations: list[StationReport] = field(default_factory=list)
    buffers: list[tuple[str, float, int, int]] = field(default_factory=list)
    lead_times: list[float] = field(default_factory=list)

    # ------------------------------------------------------------- metrics

    @property
    def throughput_per_hour(self) -> float:
        return self.throughput / (self.duration / HOUR) if self.duration else 0.0

    @property
    def avg_lead_time(self) -> float:
        return sum(self.lead_times) / len(self.lead_times) if self.lead_times else 0.0

    @property
    def avg_wip(self) -> float:
        return sum(level for _, level, _, _ in self.buffers)

    @property
    def bottleneck(self) -> StationReport | None:
        return max(self.stations, key=lambda s: s.bottleneck_score, default=None)

    def to_dict(self) -> dict[str, Any]:
        bn = self.bottleneck
        return {
            "duration_s": self.duration,
            "events": self.events,
            "created": self.created,
            "throughput": self.throughput,
            "rejected_at_source": self.rejected,
            "throughput_per_hour": round(self.throughput_per_hour, 2),
            "avg_lead_time_s": round(self.avg_lead_time, 2),
            "avg_wip": round(self.avg_wip, 2),
            "bottleneck": bn.name if bn else None,
            "stations": [
                {
                    "name": s.name, "processed": s.processed,
                    "utilisation": round(s.utilisation, 4),
                    "blocked": round(s.blocked, 4),
                    "starved": round(s.starved, 4),
                    "down": round(s.down, 4),
                    "failures": s.failures,
                }
                for s in self.stations
            ],
            "buffers": [
                {"name": n, "avg_level": round(lvl, 2), "peak": pk, "capacity": cap}
                for n, lvl, pk, cap in self.buffers
            ],
        }

    # -------------------------------------------------------------- render

    def render(self) -> str:
        lines: list[str] = []
        add = lines.append
        add(_G["heavy"] * 72)
        add(f"  SIMULATION REPORT     {fmt_time(self.duration)} simulated"
            f"     {self.events:,} events")
        add(_G["heavy"] * 72)
        add("")
        add(f"  Throughput        {self.throughput:>10,} parts"
            f"   ({self.throughput_per_hour:,.1f}/hr)")
        add(f"  Created           {self.created:>10,} parts")
        if self.rejected:
            add(f"  Rejected at source{self.rejected:>10,} parts   (line backed up)")
        add(f"  Avg lead time     {fmt_time(self.avg_lead_time):>10}")
        add(f"  Avg WIP           {self.avg_wip:>10.1f} parts")
        bn = self.bottleneck
        if bn:
            add(f"  Bottleneck        {bn.name:>10}   ({bn.utilisation:.0%} busy)")
        add("")
        add("  STATION                 PROC     BUSY   BLOCKED   STARVED    DOWN  FAIL")
        add("  " + _G["light"] * 70)
        for s in self.stations:
            marker = _G["mark"] if bn and s.name == bn.name else ""
            add(f"  {s.name:<20} {s.processed:>7,}  {s.utilisation:>6.1%}   "
                f"{s.blocked:>6.1%}    {s.starved:>6.1%}  {s.down:>5.1%} {s.failures:>4}{marker}")
        if self.buffers:
            add("")
            add("  BUFFER                   AVG     PEAK   CAPACITY")
            add("  " + _G["light"] * 70)
            for name, level, peak, cap in self.buffers:
                flag = f'  {_G["arrow"]} saturated' if cap and peak >= cap else ""
                add(f"  {name:<20} {level:>8.1f} {peak:>8} {cap:>10}{flag}")
        add("")
        add(_G["heavy"] * 72)
        return "\n".join(lines)


def analyse(engine) -> Report:
    """Build a :class:`Report` from a finished engine run."""
    root = engine.root
    total = engine.now
    report = Report(duration=total, events=engine.events_processed)

    for obj in root.walk():
        if isinstance(obj, Station):
            t = obj.tracker
            report.stations.append(StationReport(
                name=obj.name,
                processed=obj.processed,
                utilisation=t.fraction(RUNNING, total),
                blocked=t.fraction(BLOCKED, total),
                starved=t.fraction(STARVED, total),
                down=t.fraction(DOWN, total),
                failures=getattr(obj, "failures", 0),
            ))
        elif isinstance(obj, Buffer):
            report.buffers.append(
                (obj.name, obj.average_level(total), obj.peak, obj.capacity)
            )
        elif isinstance(obj, Sink):
            report.throughput += obj.throughput
            report.lead_times.extend(obj.lead_times())
        elif isinstance(obj, Source):
            report.created += obj.created
            report.rejected += obj.rejected

    return report
