"""Tests for the object model, the event engine and material flow."""

import pytest

from twinforge import (
    Area,
    Buffer,
    Factory,
    Machine,
    SimulationEngine,
    Sink,
    Source,
    TwinObject,
    analyse,
    duration,
    loads,
)
from twinforge.sim import HOUR, MINUTE

# ------------------------------------------------------------------ core

def test_tree_parenting_and_paths():
    root = Factory("Plant")
    line = Area("Line_A")
    m = Machine("CNC_01")
    root.add(line)
    line.add(m)
    assert m.parent is line and line.parent is root
    assert m.path == "Plant/Line_A/CNC_01"
    assert root.find("CNC_01") is m
    assert root.find("nope") is None
    assert len(list(root.walk())) == 3


def test_add_reparents_instead_of_duplicating():
    a, b = Area("A"), Area("B")
    child = Machine("M")
    a.add(child)
    b.add(child)
    assert child.parent is b
    assert child not in a.children
    assert child in b.children


def test_signals_connect_emit_disconnect():
    obj = TwinObject("thing")
    seen = []
    handler = obj.signal("ping").connect(lambda v: seen.append(v))
    obj.emit("ping", 1)
    obj.emit("ping", 2)
    obj.signal("ping").disconnect(handler)
    obj.emit("ping", 3)
    assert seen == [1, 2]


def test_emitting_unknown_signal_is_safe():
    TwinObject("x").emit("never_declared")


# -------------------------------------------------------------- durations

@pytest.mark.parametrize("text,expected", [
    (42, 42.0), ("42", 42.0), ("42s", 42.0), ("2min", 120.0),
    ("1.5h", 5400.0), ("2d", 172800.0), ("30 min", 1800.0),
])
def test_duration_parsing(text, expected):
    assert duration(text) == expected


def test_duration_rejects_unknown_unit():
    with pytest.raises(ValueError):
        duration("10 fortnights")


# ----------------------------------------------------------------- engine

def test_events_fire_in_time_order():
    eng = SimulationEngine()
    order = []
    eng.schedule(30, lambda: order.append("c"))
    eng.schedule(10, lambda: order.append("a"))
    eng.schedule(20, lambda: order.append("b"))
    eng.run(until=60)
    assert order == ["a", "b", "c"]


def test_simultaneous_events_keep_insertion_order():
    eng = SimulationEngine()
    order = []
    for i in range(5):
        eng.schedule(10, lambda i=i: order.append(i))
    eng.run(until=20)
    assert order == [0, 1, 2, 3, 4]


def test_run_stops_at_horizon():
    eng = SimulationEngine()
    fired = []
    eng.schedule(5, lambda: fired.append("in"))
    eng.schedule(500, lambda: fired.append("out"))
    eng.run(until=100)
    assert fired == ["in"]


def test_cannot_schedule_into_the_past():
    eng = SimulationEngine()
    with pytest.raises(ValueError):
        eng.schedule(-1, lambda: None)


# ------------------------------------------------------------------ flow

def _simple_line(capacity=5, cycle="10s", interval="10s"):
    b_in, b_out = Buffer("B_in", capacity=capacity), Buffer("B_out", capacity=100)
    src = Source("Src", interval=interval).feeds(b_in)
    mac = Machine("M1", cycle_time=cycle).fed_by(b_in).feeds(b_out)
    sink = Sink("Out").fed_by(b_out)
    return Factory("F").add(Area("L").add(b_in, b_out, src, mac, sink)), sink, mac


def test_parts_flow_end_to_end():
    root, sink, _ = _simple_line()
    SimulationEngine(root, seed=1).run(HOUR)
    assert sink.throughput > 0
    assert all(p.completed_at is not None for p in sink.completed)
    assert all(p.lead_time >= 0 for p in sink.completed)


def test_no_parts_are_lost():
    """Everything created is either finished or still somewhere in the line."""
    root, sink, _ = _simple_line(capacity=3, cycle="20s", interval="5s")
    eng = SimulationEngine(root, seed=7)
    eng.run(HOUR)
    src = root.find("Src")
    in_buffers = sum(len(b.items) for b in root.walk() if isinstance(b, Buffer))
    in_machines = sum(
        1 for m in root.walk()
        if isinstance(m, Machine) and (m.current is not None or m._pending is not None)
    )
    assert src.created == sink.throughput + in_buffers + in_machines


def test_slow_machine_creates_backpressure():
    """A machine slower than the source must fill its input buffer."""
    root, sink, _ = _simple_line(capacity=4, cycle="60s", interval="5s")
    SimulationEngine(root, seed=3).run(HOUR)
    assert root.find("B_in").peak == 4          # saturated
    assert root.find("Src").rejected > 0        # and the source is turned away


def test_deep_line_does_not_recurse():
    """Long chains must not blow the stack: flow goes through the event queue."""
    root = Factory("Deep")
    area = Area("Line")
    root.add(area)
    prev = Buffer("B0", capacity=2)
    area.add(prev)
    src = Source("Src", interval="1s").feeds(prev)
    area.add(src)
    for i in range(60):                          # 60 stations back to back
        nxt = Buffer(f"B{i+1}", capacity=2)
        m = Machine(f"M{i}", cycle_time="1s").fed_by(prev).feeds(nxt)
        area.add(nxt, m)
        prev = nxt
    sink = Sink("End").fed_by(prev)
    area.add(sink)
    SimulationEngine(root, seed=1).run(10 * MINUTE)   # would RecursionError if coupled
    assert sink.throughput > 0


def test_runs_are_reproducible_by_seed():
    def once(seed):
        root, sink, _ = _simple_line(cycle={"normal": {"mean": "10s", "sd": "3s"}})
        SimulationEngine(root, seed=seed).run(HOUR)
        return sink.throughput

    assert once(42) == once(42)


def test_machine_failures_reduce_output():
    root_a, sink_a, _ = _simple_line(cycle="10s")
    SimulationEngine(root_a, seed=5).run(4 * HOUR)

    b_in, b_out = Buffer("B_in", capacity=5), Buffer("B_out", capacity=100)
    src = Source("Src", interval="10s").feeds(b_in)
    mac = Machine("M1", cycle_time="10s", mtbf="5min", mttr="2min").fed_by(b_in).feeds(b_out)
    sink_b = Sink("Out").fed_by(b_out)
    root_b = Factory("F").add(Area("L").add(b_in, b_out, src, mac, sink_b))
    SimulationEngine(root_b, seed=5).run(4 * HOUR)

    assert mac.failures > 0
    assert sink_b.throughput < sink_a.throughput


# -------------------------------------------------------------- analytics

def test_report_identifies_the_bottleneck():
    """The slow station should be named, not merely have poor numbers."""
    b1 = Buffer("B1", capacity=50)
    b2 = Buffer("B2", capacity=2)
    b3 = Buffer("B3", capacity=50)
    src = Source("Src", interval="5s").feeds(b1)
    fast = Machine("Fast", cycle_time="5s").fed_by(b1).feeds(b2)
    slow = Machine("Slow", cycle_time="30s").fed_by(b2).feeds(b3)
    sink = Sink("Out").fed_by(b3)
    root = Factory("F").add(Area("L").add(b1, b2, b3, src, fast, slow, sink))

    eng = SimulationEngine(root, seed=1)
    eng.run(2 * HOUR)
    report = analyse(eng)

    assert report.bottleneck.name == "Slow"
    assert report.bottleneck.utilisation > 0.85
    assert report.throughput > 0
    # the fast machine should show blocking, not starvation
    fast_report = next(s for s in report.stations if s.name == "Fast")
    assert fast_report.blocked > fast_report.starved


def test_state_fractions_are_bounded():
    root, _, _ = _simple_line()
    eng = SimulationEngine(root, seed=2)
    eng.run(HOUR)
    for s in analyse(eng).stations:
        total = s.utilisation + s.blocked + s.starved + s.down
        assert 0.0 <= total <= 1.0001, f"{s.name} state time exceeds wall time"


# ----------------------------------------------------------------- format

TWIN = """
version: 1
twin:
  type: Factory
  name: Tiny
  children:
    - type: Source
      name: S
      properties: {interval: 10s}
    - type: Buffer
      name: B
      properties: {capacity: 5}
    - type: Machine
      name: M
      properties: {cycle_time: 15s}
    - type: Buffer
      name: B2
      properties: {capacity: 20}
    - type: Sink
      name: K
connections:
  - {from: S, to: B}
  - {from: B, to: M}
  - {from: M, to: B2}
  - {from: B2, to: K}
"""


def test_twin_file_round_trip_and_run():
    root = loads(TWIN)
    assert root.name == "Tiny"
    assert isinstance(root.find("M"), Machine)
    eng = SimulationEngine(root, seed=1)
    eng.run(HOUR)
    assert root.find("K").throughput > 0


def test_unknown_type_is_reported_clearly():
    from twinforge import TwinFormatError
    with pytest.raises(TwinFormatError, match="unknown object type"):
        loads("version: 1\ntwin: {type: Warp_Drive, name: X}")


def test_illegal_connection_is_rejected():
    from twinforge import TwinFormatError
    bad = """
version: 1
twin:
  type: Factory
  name: F
  children:
    - {type: Machine, name: A}
    - {type: Machine, name: B}
connections:
  - {from: A, to: B}
"""
    with pytest.raises(TwinFormatError, match="material moves through Buffers"):
        loads(bad)


# --------------------------------------------------------------------- ui

def test_recorder_captures_a_replayable_event_log():
    from twinforge.ui import Recorder, export_layout

    root = loads(TWIN)
    recorder = Recorder(root)          # must attach before the engine binds
    eng = SimulationEngine(root, seed=1)
    eng.run(HOUR)

    events = recorder.timeline()
    assert events, "a hidden hour of simulation should produce events"
    assert all(events[i]["t"] <= events[i + 1]["t"] for i in range(len(events) - 1))

    kinds = {e["type"] for e in events}
    assert "cycle_started" in kinds
    assert "received" in kinds and "released" in kinds

    layout = export_layout(root)
    node_paths = {n["path"] for n in layout["nodes"]}
    assert {e["path"] for e in events} <= node_paths
    assert any(e["type"] == "completed" for e in events)


def test_ui_render_produces_self_contained_html():
    from twinforge.ui import Recorder, export_layout, render

    root = loads(TWIN)
    recorder = Recorder(root)
    eng = SimulationEngine(root, seed=1)
    eng.run(HOUR)

    payload = {
        "meta": {"name": root.name, "duration": HOUR, "seed": 1},
        "layout": export_layout(root),
        "events": recorder.timeline(),
        "report": analyse(eng).to_dict(),
    }
    html = render(payload, title="Tiny — Replay")
    assert "<svg" in html
    assert "Tiny" in html
    assert '"nodes"' in html and '"events"' in html
    assert "<script>" in html and "</html>" in html
