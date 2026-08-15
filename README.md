# TwinForge

**An engine for building, simulating and analysing industrial digital twins.**

Godot gives you nodes, scenes and a scene tree, and people build wildly different
games with them. TwinForge gives you the same idea with industrial primitives:
compose a factory from objects in a tree, connect material flow, run it, and get
the numbers back.

```bash
pip install twinforge
twin run examples/line_a.twin --for 8h
```

```
════════════════════════════════════════════════════════════════════════
  SIMULATION REPORT     08:00:00 simulated     12,989 events
════════════════════════════════════════════════════════════════════════

  Throughput               468 parts   (58.5/hr)
  Created                  503 parts
  Rejected at source       662 parts   (line backed up)
  Avg lead time       00:31:26
  Avg WIP                 30.3 parts
  Bottleneck            CNC_02   (89% busy)

  STATION                 PROC     BUSY   BLOCKED   STARVED    DOWN  FAIL
  ──────────────────────────────────────────────────────────────────────
  CNC_01                   477   65.2%    20.9%      0.0%  12.0%   13
  CNC_02                   468   89.1%     0.0%      2.0%   8.9%    5 ◀
  Transfer                 468   19.5%     0.0%     80.5%   0.0%    0
  Inspection               468   27.8%     0.0%     72.2%   0.0%    0

  BUFFER                   AVG     PEAK   CAPACITY
  ──────────────────────────────────────────────────────────────────────
  Buffer_In                23.9       25         25  ← saturated
  Buffer_Mid                6.4        8          8  ← saturated
  Buffer_Xfer               0.0        1          6

════════════════════════════════════════════════════════════════════════
```

Read that report and you already know the story: **CNC_02 is the constraint.**
It is 89% busy and never starved, CNC_01 is blocked 20.9% of the time waiting to
hand work over, the buffer between them is pinned at capacity, and everything
downstream sits starved. Adding a third inspection station would achieve
nothing. That is the kind of answer a twin is for.

---

## Why this exists

Most factory simulators are either €40,000 licences or 200-line scripts nobody
can reuse. TwinForge aims at the gap: something an engineer can install in one
command, describe a line in a readable file, and get a defensible answer from —
while staying a real library you can build products on top of.

- **No GUI required.** The engine runs headless. Ten thousand scenarios don't
  render a single polygon.
- **Readable model files.** A `.twin` file is YAML. Diff it, review it, generate it.
- **Fast enough to sweep.** ~486,000 events/sec — a week of factory time in half
  a second, a full 8-hour shift in 86 ms.
- **Deterministic.** Same seed, same run, every time. Essential when you're
  comparing scenarios rather than admiring animations.

## Install

```bash
pip install twinforge          # or: pip install -e .  from a clone
```

Python 3.9+. One dependency (PyYAML). No compiler, no toolchain.

## Two ways to build a twin

**In a file**, when you want something reviewable:

```yaml
version: 1
twin:
  type: Factory
  name: Demo_Plant
  children:
    - type: Source
      name: Raw_Intake
      properties: {interval: {exponential: {mean: 25s}}}
    - type: Buffer
      name: Buffer_In
      properties: {capacity: 25}
    - type: Machine
      name: CNC_01
      properties:
        cycle_time: {normal: {mean: 40s, sd: 4s}}
        mtbf: 45min
        mttr: {normal: {mean: 6min, sd: 90s}}
    - type: Buffer
      name: Buffer_Out
      properties: {capacity: 50}
    - type: Sink
      name: Shipping

connections:
  - {from: Raw_Intake, to: Buffer_In}
  - {from: Buffer_In,  to: CNC_01}
  - {from: CNC_01,     to: Buffer_Out}
  - {from: Buffer_Out, to: Shipping}
```

**In Python**, when you want to generate or parameterise it:

```python
from twinforge import Factory, Area, Source, Machine, Buffer, Sink
from twinforge import SimulationEngine, analyse

b_in  = Buffer("Buffer_In", capacity=25)
b_out = Buffer("Buffer_Out", capacity=50)

line = Area("Line_A").add(
    b_in, b_out,
    Source("Raw_Intake", interval="25s").feeds(b_in),
    Machine("CNC_01", cycle_time="40s", mtbf="45min", mttr="6min")
        .fed_by(b_in).feeds(b_out),
    Sink("Shipping").fed_by(b_out),
)

engine = SimulationEngine(Factory("Demo_Plant").add(line), seed=1)
engine.run(8 * 3600)
print(analyse(engine).render())
```

## The object model

Everything is a `TwinObject` in a tree — the tree is *ownership*, the connections
are *material flow*. A machine belongs to one line but can feed several buffers.

| Object | What it does |
|---|---|
| `Factory`, `Area` | Grouping. A plant, a line, a cell, a warehouse. |
| `Source` | Introduces material at an interval. |
| `Buffer` | Bounded store. **Capacity is what creates blocking.** |
| `Machine` | Processes a part over a cycle time. Optional `mtbf`/`mttr`. |
| `Conveyor` | Transport with a travel time. |
| `Sink` | Collects finished parts and records lead time. |
| `Worker` | A named operator, for manning models. |

### Flow is pull-with-blocking

Because that is how real lines behave:

- a station **pulls** work upstream when it's free,
- it **blocks** when the downstream buffer is full,
- it **starves** when the upstream buffer is empty.

Blocked and starved time are tracked as first-class state, not inferred
afterwards — which is exactly why the report can name the bottleneck instead of
just showing you utilisation and leaving you to guess.

### Signals

Objects talk through signals, so you can hook behaviour on without subclassing:

```python
cnc.signal("failed").connect(lambda m: print(f"{m.name} went down"))
cnc.signal("cycle_completed").connect(count_part)
```

Available on stations: `cycle_started`, `cycle_completed`, `blocked`, `starved`,
`failed`, `repaired`. On buffers: `received`, `released`, `full`, `empty`.

### Distributions

Anywhere a duration is accepted you can give a constant or a distribution:

```yaml
cycle_time: 42s
cycle_time: {normal:      {mean: 40s, sd: 4s}}
cycle_time: {exponential: {mean: 25s}}
cycle_time: {uniform:     {low: 10s, high: 20s}}
cycle_time: {triangular:  {low: 12s, mode: 15s, high: 24s}}
```

## The CLI

```bash
twin show  examples/line_a.twin            # print the object tree
twin run   examples/line_a.twin --for 8h   # simulate and report
twin run   examples/line_a.twin --json -o out.json
twin sweep examples/line_a.twin --runs 20  # variance across seeds
```

`sweep` is the one people underestimate. A single run of a stochastic model is
an anecdote:

```
  seed   throughput   per hour    lead time        bottleneck
──────────────────────────────────────────────────────────────
     1          468       58.5     00:31:26            CNC_02
     2          455       56.9     00:33:04            CNC_02
     3          471       58.9     00:30:11            CNC_02
──────────────────────────────────────────────────────────────
  mean        464.7              spread 16 across 20 runs
```

## Extending it

Add your own object type and register it — the `.twin` loader picks it up:

```python
from twinforge import Machine, REGISTRY

class PaintBooth(Machine):
    type_name = "PaintBooth"

    def setup(self):
        super().setup()
        self.signal("cycle_completed").connect(self._log_voc)

    def _log_voc(self, part):
        self.metadata.setdefault("voc_grams", 0)
        self.metadata["voc_grams"] += 12

REGISTRY["PaintBooth"] = PaintBooth
```

## Status and roadmap

**v0.1 — this release.** Object model, discrete-event engine, `.twin` format,
material flow with blocking and starvation, reliability, KPI and bottleneck
analysis, CLI. Headless and tested.

Next, roughly in order: a live 2D layout view, a plugin SDK, an asset library
of reusable cells, 3D visualisation, live OPC-UA/MQTT connections for true
digital twins, and an optimisation layer.

The engine is deliberately independent of any viewer. Rendering is a consumer of
the model, never a prerequisite for it.

## Tests

```bash
pip install -e ".[dev]"
pytest -q
```

27 tests covering the tree, signals, event ordering, material conservation,
backpressure, determinism, bottleneck detection and the file format — including
a 60-station chain that verifies flow never recurses.

## Licence

MIT. Use it for your hackathon, your thesis, or your plant.
