"""Compare the two suggestion engines on many random invented weeks. Reads nothing, writes nothing.

uv run python scripts/shifts/compare_engines.py [weeks]
"""

from __future__ import annotations

import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(
    0, str(Path(__file__).resolve().parents[2] / "tests" / "unit" / "domain")
)

from app.domain.shifts.suggest import suggest  # noqa: E402
from app.domain.shifts.suggest_cpsat import suggest_cpsat  # noqa: E402
from test_suggest_cpsat import random_week  # noqa: E402


def measure(data, run):
    started = time.perf_counter()
    out = run(data)
    seconds = time.perf_counter() - started
    # A person counts as having a shift when they already had one this week too.
    load = Counter({q.volunteer_id: q.week_shifts for q in data.people})
    load.update(p.volunteer_id for p in out.picks)
    asked = {
        q.volunteer_id
        for q in data.people
        if any(
            (p.slot_id, q.volunteer_id) in data.allowed
            and data.answers.get((p.shift_id, q.volunteer_id)) in ("vil", "kan")
            for p in data.places
        )
    }
    return {
        "filled": len(out.picks),
        "served": sum(1 for v in asked if load[v] > 0),
        "asked": len(asked),
        "most": max(load.values(), default=0),
        "seconds": seconds,
    }


def main(weeks: int) -> None:
    totals = {"simple": Counter(), "cpsat": Counter()}
    better = worse = same = 0
    for seed in range(weeks):
        data = random_week(seed)
        a = measure(data, suggest)
        b = measure(data, suggest_cpsat)
        for name, m in (("simple", a), ("cpsat", b)):
            totals[name].update(m)
        key_a, key_b = (a["filled"], a["served"]), (b["filled"], b["served"])
        better += key_b > key_a
        worse += key_b < key_a
        same += key_b == key_a
    print(f"{weeks} invented weeks")
    for name, m in totals.items():
        print(
            f"{name:7} places filled {m['filled']:5}  people with a shift {m['served']:5}"
            f" of {m['asked']}  busiest person (sum) {m['most']:4}  time {m['seconds']:.2f}s"
        )
    print(f"solver better on {better}, equal on {same}, worse on {worse} weeks")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 200)
