"""Command line interface: ``twin run factory.twin --for 8h``."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .analytics import analyse, set_ascii
from .sim import SimulationEngine, duration, fmt_time
from .twinfile import TwinFormatError, load

# Windows consoles default to a legacy code page that cannot encode box-drawing
# characters. Try UTF-8 first; fall back to ASCII glyphs if that is impossible.
_UNICODE_OK = True


def _init_output() -> None:
    global _UNICODE_OK
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except (AttributeError, OSError):
            pass
    enc = (getattr(sys.stdout, "encoding", "") or "").lower()
    _UNICODE_OK = "utf" in enc
    set_ascii(not _UNICODE_OK)


def _glyphs() -> dict[str, str]:
    if _UNICODE_OK:
        return {"last": "└─ ", "mid": "├─ ", "pipe": "│  "}
    return {"last": "`- ", "mid": "|- ", "pipe": "|  "}


def _tree(obj, prefix: str = "", last: bool = True, out=None) -> None:
    out = out if out is not None else sys.stdout
    g = _glyphs()
    connector = g["last"] if last else g["mid"]
    label = f"{obj.name}  [{obj.type_name}]"
    out.write(f"{prefix}{connector if prefix else ''}{label}\n")
    child_prefix = prefix + ("   " if last else g["pipe"]) if prefix else "  "
    for i, child in enumerate(obj.children):
        _tree(child, child_prefix, i == len(obj.children) - 1, out)


def cmd_run(args: argparse.Namespace) -> int:
    root = load(args.twin)
    engine = SimulationEngine(root, seed=args.seed)
    engine.log_enabled = args.trace
    horizon = duration(args.duration)

    if not args.quiet:
        print(f"▶ simulating {root.name} for {fmt_time(horizon)}"
              f"  (seed={args.seed})", file=sys.stderr)

    engine.run(horizon)
    report = analyse(engine)

    if args.json:
        payload = report.to_dict()
        text = json.dumps(payload, indent=2)
        if args.output:
            Path(args.output).write_text(text, encoding="utf-8")
            print(f"wrote {args.output}", file=sys.stderr)
        else:
            print(text)
    else:
        print(report.render())

    if args.trace:
        print("\n  EVENT TRACE", file=sys.stderr)
        for when, source, message in engine.log:
            print(f"  {fmt_time(when)}  {source:<28} {message}", file=sys.stderr)
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    root = load(args.twin)
    print(f"\n{root.name}  [{root.type_name}]")
    for i, child in enumerate(root.children):
        _tree(child, "  ", i == len(root.children) - 1)
    counts: dict[str, int] = {}
    for obj in root.walk():
        counts[obj.type_name] = counts.get(obj.type_name, 0) + 1
    print("\n  " + "  ".join(f"{k}×{v}" for k, v in sorted(counts.items())) + "\n")
    return 0


def cmd_sweep(args: argparse.Namespace) -> int:
    """Run the same twin under many seeds — one line of output per run."""
    horizon = duration(args.duration)
    print(f"{'seed':>6}  {'throughput':>11}  {'per hour':>9}  "
          f"{'lead time':>11}  {'bottleneck':>16}")
    print("─" * 62)
    totals = []
    for seed in range(args.seed, args.seed + args.runs):
        engine = SimulationEngine(load(args.twin), seed=seed)
        engine.run(horizon)
        r = analyse(engine)
        bn = r.bottleneck
        totals.append(r.throughput)
        print(f"{seed:>6}  {r.throughput:>11,}  {r.throughput_per_hour:>9,.1f}  "
              f"{fmt_time(r.avg_lead_time):>11}  {(bn.name if bn else '-'):>16}")
    if totals:
        mean = sum(totals) / len(totals)
        spread = max(totals) - min(totals)
        print("─" * 62)
        print(f"{'mean':>6}  {mean:>11,.1f}  "
              f"{'':>9}  spread {spread:,} across {len(totals)} runs")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="twin",
        description="TwinForge — an engine for building and simulating industrial digital twins.",
    )
    p.add_argument("--version", action="version", version=f"twinforge {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="simulate a twin and report KPIs")
    run.add_argument("twin", help="path to a .twin file")
    run.add_argument("--for", "--duration", dest="duration", default="8h",
                     help="how long to simulate, e.g. 8h, 30min, 3d (default: 8h)")
    run.add_argument("--seed", type=int, default=1, help="RNG seed (default: 1)")
    run.add_argument("--json", action="store_true", help="emit JSON instead of a table")
    run.add_argument("-o", "--output", help="write JSON to a file")
    run.add_argument("--trace", action="store_true", help="print an event trace")
    run.add_argument("-q", "--quiet", action="store_true")
    run.set_defaults(func=cmd_run)

    show = sub.add_parser("show", help="print the twin tree")
    show.add_argument("twin")
    show.set_defaults(func=cmd_show)

    sweep = sub.add_parser("sweep", help="repeat a run across seeds to see variance")
    sweep.add_argument("twin")
    sweep.add_argument("--for", "--duration", dest="duration", default="8h")
    sweep.add_argument("--runs", type=int, default=10)
    sweep.add_argument("--seed", type=int, default=1)
    sweep.set_defaults(func=cmd_sweep)

    return p


def main(argv: list[str] | None = None) -> int:
    _init_output()
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (TwinFormatError, FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
