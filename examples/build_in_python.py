"""Building and sweeping a twin from Python instead of a .twin file.

Answers a real question: does adding a second slow machine beat enlarging the
buffer in front of it? Run with:  python examples/build_in_python.py
"""

from twinforge import (
    Area, Buffer, Factory, Machine, Sink, SimulationEngine, Source, analyse,
)
from twinforge.sim import HOUR


def build(slow_machines: int = 1, mid_buffer: int = 5) -> Factory:
    b_in = Buffer("Buffer_In", capacity=40)
    b_mid = Buffer("Buffer_Mid", capacity=mid_buffer)
    b_out = Buffer("Buffer_Out", capacity=200)

    line = Area("Line").add(
        b_in, b_mid, b_out,
        Source("Intake", interval="20s").feeds(b_in),
        Machine("Fast", cycle_time="18s").fed_by(b_in).feeds(b_mid),
        Sink("Shipping").fed_by(b_out),
    )
    # every slow machine pulls from the same buffer — that is how you model
    # parallel capacity at one stage
    for i in range(slow_machines):
        line.add(
            Machine(f"Slow_{i+1}", cycle_time="45s", mtbf="60min", mttr="8min")
            .fed_by(b_mid).feeds(b_out)
        )
    return Factory("Options").add(line)


def throughput(**kwargs) -> float:
    total = 0.0
    for seed in range(8):                      # average out the randomness
        eng = SimulationEngine(build(**kwargs), seed=seed)
        eng.run(8 * HOUR)
        total += analyse(eng).throughput
    return total / 8


if __name__ == "__main__":
    print("scenario                          parts / 8h shift")
    print("-" * 52)
    base = throughput(slow_machines=1, mid_buffer=5)
    print(f"  baseline (1 slow, buffer 5)     {base:>10.0f}")

    bigger = throughput(slow_machines=1, mid_buffer=40)
    print(f"  bigger buffer (40)              {bigger:>10.0f}   "
          f"{(bigger - base) / base:+.1%}")

    second = throughput(slow_machines=2, mid_buffer=5)
    print(f"  second slow machine             {second:>10.0f}   "
          f"{(second - base) / base:+.1%}")

    print("\nBuffers hide a constraint; they do not remove it.")
