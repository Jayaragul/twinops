"""TwinForge — an engine for building, simulating and analysing digital twins.

Quick start::

    from twinforge import Factory, Area, Source, Machine, Buffer, Sink
    from twinforge import SimulationEngine, analyse

    line = Area("Line_A").add(
        Source("Intake", interval="20s").feeds(b1 := Buffer("B1", capacity=20)),
        Machine("CNC_01", cycle_time="42s", mtbf="40min", mttr="8min")
            .fed_by(b1).feeds(b2 := Buffer("B2", capacity=10)),
        Sink("Shipping").fed_by(b2),
    )
    report = analyse(SimulationEngine(Factory("Demo").add(line), seed=1).run(8 * 3600))
    print(report.render())
"""

__version__ = "0.1.0"

from .analytics import Report, StationReport, analyse
from .core import Signal, TwinObject
from .objects import (
    REGISTRY,
    Area,
    Buffer,
    Conveyor,
    Factory,
    Machine,
    Sink,
    Source,
    Station,
    Worker,
)
from .sim import DAY, HOUR, MINUTE, SECOND, Part, SimulationEngine, duration, fmt_time
from .twinfile import TwinFormatError, dumps, load, loads, save

__all__ = [
    "__version__",
    # core
    "TwinObject", "Signal",
    # objects
    "Factory", "Area", "Machine", "Buffer", "Source", "Sink", "Conveyor", "Worker",
    "Station", "REGISTRY",
    # simulation
    "SimulationEngine", "Part", "duration", "fmt_time",
    "SECOND", "MINUTE", "HOUR", "DAY",
    # analytics
    "analyse", "Report", "StationReport",
    # format
    "load", "loads", "save", "dumps", "TwinFormatError",
]
