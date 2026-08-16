# Contributing to TwinOps

Thanks for looking. This project is young enough that almost any contribution
moves it forward, and small ones are genuinely welcome.

## The fastest useful thing you can do

**Tell us about a real production line.** Open a
[Model my line](https://github.com/Jayaragul/twinops/issues/new?template=my_factory.md)
issue and describe what you make, the steps involved, and where parts pile up.

No code required, and it's more valuable than it sounds. A simulator that only
gets tested against invented examples slowly drifts away from how factories
actually behave. Real lines keep it honest.

If you can compare TwinOps against **real measured throughput** and tell us where
it's wrong — that's the single highest-value contribution anyone can make right
now. Correctness beats features.

## Getting set up

The Studio is `docs/index.html`. It has no build step and no dependencies — open
it in a browser and edit it. To exercise it over `http://` rather than `file://`:

```bash
python -m http.server 8000
# then open http://localhost:8000/docs/
```

The Python engine:

```bash
pip install -e ".[dev]"
pytest -q          # 30 tests, well under a second
ruff check .
```

## How the pieces fit

```
twinops/core.py       TwinObject tree + signals
twinops/sim.py        event queue, clock, RNG, distributions
twinops/objects.py    Machine, Buffer, Source, Sink, Conveyor
twinops/analytics.py  KPIs and bottleneck detection
twinops/twinfile.py   the .twin file format
twinops/ui.py         records a run, renders a standalone HTML replay
docs/index.html       the Studio (its own JS engine, mirroring objects.py)
```

**There are two engines and they must agree.** The Python engine is the reference;
the Studio has a JavaScript port so it can run entirely in the browser with nothing
installed. If you change simulation behaviour in one, change it in the other, and
add a test in `tests/test_engine.py` that pins the behaviour down.

Yes, that's duplication. It buys the thing that makes TwinOps worth using — a
zero-install tool — and it's a deliberate trade, not an oversight. If you have a
clean way to share one engine between both without adding a build step or a
server, that's a conversation worth having.

## Two rules the engine depends on

Both of these are load-bearing. Breaking either produces bugs that look like
plausible output rather than crashes, so please keep them in mind.

**1. Signal handlers must not do work directly.** A push cascades into a pull,
which cascades into another push. Do that synchronously and a long line recurses
until the stack dies. Handlers only queue a zero-delay wake-up on the event queue
(`_nudge`), which turns recursion into iteration and keeps ordering deterministic.
`test_deep_line_does_not_recurse` guards this.

**2. Notification order rotates.** When several stations pull from one buffer, a
fixed order means whoever subscribed first wins every race — one machine runs at
92% while an identical one idles at 8%. `Signal(rotate=True)` shares the work.
`test_parallel_machines_share_work_fairly` guards this.

## Style

- Match the surrounding code. No formatter beyond `ruff`.
- Comments explain **why**, not what. If the reason is obvious from the code, skip it.
- Prefer a test that would have caught the bug over a comment describing it.
- Plain language in the UI. A production supervisor should understand every label
  without a glossary — "How many can it hold?" rather than "Buffer capacity".

## Pull requests

Small and focused beats large and comprehensive. Say what you changed and why;
if it changes simulation behaviour, include a before/after number.

Run `pytest -q` and `ruff check .` first. If you touched the Studio, actually open
it and click through the thing you changed — it has no test suite yet, and adding
one is itself a welcome contribution.

## Licence

MIT. By contributing you agree your work ships under it.
