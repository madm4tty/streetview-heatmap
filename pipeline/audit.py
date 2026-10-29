"""Audit the monthly refresh: how much new imagery would its sentinels miss?

A refresh re-checks one sentinel per 250 m cell and re-samples a road only if
a sentinel in a cell it touches changed. This picks random points along the
roads (uniform by length, from a fixed seed so a re-run picks the same ones),
looks each up fresh, and looks up the sentinels of every cell its road
touches, exactly as a refresh would. For each point:

- same:  Google gives what the map shows
- newer: newer imagery than the map shows, or imagery where it had none;
         "caught" if a refresh run now would re-sample the road, else "missed"
- other: anything else (an older date, imagery gone, user photos instead)

It also sorts the sentinel changes it saw, since many refresh re-samples find
nothing newer. Nothing is saved: lookups go to a throwaway cache, and the
pipeline's own cache and results are only read.
"""

import random
from bisect import bisect_right
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from itertools import accumulate
from typing import Dict, Iterable, List, Optional

from pipeline.export import month_index
from pipeline.overview import band_of
from pipeline.refresh import plan, segment_cells
from pipeline.roads import PATH_HIGHWAYS
from pipeline.sampler import Resolver, key_at
from pipeline.streetview import NO_COVERAGE, NON_GOOGLE

# Keep probes this far inside a piece, away from where bisection put a boundary
INSET_M = 10.0

MAJOR = {"motorway", "trunk", "primary", "secondary",
         "motorway_link", "trunk_link", "primary_link", "secondary_link"}
GROUPS = ("major road", "minor road", "service road", "path")
STATES = ("dated", "no imagery", "user photos")
AGE_LABELS = ("under 6 months", "6–12 months", "1–3 years", "3–5 years", "5–10 years", "over 10 years")


def road_group(highway: str) -> str:
    if highway in MAJOR:
        return "major road"
    if highway in PATH_HIGHWAYS:
        return "path"
    return "service road" if highway == "service" else "minor road"


def dated(value: str) -> bool:
    return value not in (NO_COVERAGE, NON_GOOGLE)


def state_of(value: str) -> str:
    return "dated" if dated(value) else "no imagery" if value == NO_COVERAGE else "user photos"


def compare(recorded: str, fresh: str) -> str:
    """same, newer or other: how Google's answer now compares with the map's."""
    if fresh == recorded:
        return "same"
    if dated(fresh) and (not dated(recorded) or month_index(fresh) > month_index(recorded)):
        return "newer"
    return "other"


def sentinel_change(old: str, new: str) -> str:
    """How a changed sentinel changed, to see why re-samples often find nothing newer."""
    if dated(old) and dated(new):
        return "newer date" if month_index(new) > month_index(old) else "older date"
    if not dated(old) and dated(new):
        return f"{state_of(old)} → dated"
    return f"{state_of(old)} → {state_of(new)}"


@dataclass
class AuditResult:
    probes: int = 0
    outcomes: Counter = field(default_factory=Counter)        # same, newer caught, newer missed, other
    by_group: Dict[str, Counter] = field(default_factory=lambda: defaultdict(Counter))
    by_state: Dict[str, Counter] = field(default_factory=lambda: defaultdict(Counter))
    by_age: Dict[str, Counter] = field(default_factory=lambda: defaultdict(Counter))
    sentinels: int = 0
    sentinel_changes: Counter = field(default_factory=Counter)
    lookups: int = 0

    def add(self, other: "AuditResult") -> "AuditResult":
        self.probes += other.probes
        self.outcomes += other.outcomes
        for mine, theirs in ((self.by_group, other.by_group), (self.by_state, other.by_state),
                             (self.by_age, other.by_age)):
            for key, counts in theirs.items():
                mine[key] += counts
        self.sentinels += other.sentinels
        self.sentinel_changes += other.sentinel_changes
        self.lookups += other.lookups
        return self

    def to_json(self) -> Dict:
        return {"probes": self.probes, "outcomes": dict(self.outcomes),
                "by_group": {k: dict(v) for k, v in self.by_group.items()},
                "by_state": {k: dict(v) for k, v in self.by_state.items()},
                "by_age": {k: dict(v) for k, v in self.by_age.items()},
                "sentinels": self.sentinels, "sentinel_changes": dict(self.sentinel_changes),
                "lookups": self.lookups}


async def audit(segments, old_pieces, resolve_fresh: Resolver, probes: int = 5000,
                seed: int = 1, cell_m: float = 250.0, now_index: Optional[int] = None) -> AuditResult:
    """Probe `probes` random points of the roads a refresh would keep, as above."""
    checks = plan(segments, old_pieces, cell_m)
    pieces = [p for p in checks.kept_pieces if p.end_m - p.start_m > 0]
    result = AuditResult()
    if not pieces:
        return result
    rng = random.Random(seed)
    ends = list(accumulate(p.end_m - p.start_m for p in pieces))

    # Random points, uniform by length, each inset from its piece's ends
    chosen = []
    for _ in range(probes):
        piece = pieces[min(bisect_right(ends, rng.uniform(0, ends[-1])), len(pieces) - 1)]
        inset = min(INSET_M, (piece.end_m - piece.start_m) / 4)
        seg = checks.by_id[piece.segment_id]
        chosen.append((seg, piece, key_at(seg, rng.uniform(piece.start_m + inset, piece.end_m - inset))))

    # Every sentinel a refresh would check for these roads, looked up with the probes
    cells_of = {seg.id: segment_cells(seg, checks.size) for seg, _, _ in chosen}
    cells = set().union(*cells_of.values()) & checks.sentinels.keys()
    keys = [k for _, _, k in chosen] + [checks.sentinels[c].key for c in cells]
    fresh = await resolve_fresh(list(dict.fromkeys(keys)))
    result.lookups = len(set(keys))

    changed = set()
    for cell in cells:
        sentinel = checks.sentinels[cell]
        result.sentinels += 1
        if fresh[sentinel.key] != sentinel.old_value:
            changed.add(cell)
            result.sentinel_changes[sentinel_change(sentinel.old_value, fresh[sentinel.key])] += 1

    if now_index is None:
        now_index = max((month_index(p.value) for p in pieces if dated(p.value)), default=0)
    for seg, piece, key in chosen:
        outcome = compare(piece.value, fresh[key])
        if outcome == "newer":
            outcome = "newer, caught" if cells_of[seg.id] & changed else "newer, missed"
        result.probes += 1
        result.outcomes[outcome] += 1
        result.by_group[road_group(seg.highway)][outcome] += 1
        result.by_state[state_of(piece.value)][outcome] += 1
        if dated(piece.value):
            result.by_age[AGE_LABELS[band_of(max(0, now_index - month_index(piece.value)))]][outcome] += 1
    return result


def _pct(part: int, whole: int) -> str:
    return f"{100 * part / whole:.1f}%" if whole else "–"


def report_markdown(result: AuditResult, title: str = "Refresh audit") -> str:
    """The audit as a Markdown report, for the Actions run summary."""
    o, n = result.outcomes, result.probes
    newer = o["newer, caught"] + o["newer, missed"]
    lines = [
        f"## {title}", "",
        f"{n:,} random points along the roads (uniform by length, so shares are shares of road "
        f"length), {result.sentinels:,} sentinels, {result.lookups:,} lookups.", "",
        f"- **Newer imagery than the map shows:** {_pct(newer, n)} of road",
        f"  - a refresh run now would **catch** {_pct(o['newer, caught'], n)} and "
        f"**miss {_pct(o['newer, missed'], n)}** (so it misses {_pct(o['newer, missed'], newer)} of what's newer)",
        f"- Same as the map: {_pct(o['same'], n)}",
        f"- Other differences (older date, imagery gone, user photos): {_pct(o['other'], n)}", "",
    ]

    def table(heading: str, rows: Dict[str, Counter], order: Iterable[str]) -> List[str]:
        out = [f"| {heading} | points | newer | missed | missed of newer |", "|---|---:|---:|---:|---:|"]
        for key in order:
            c = rows.get(key)
            if not c:
                continue
            total, new = sum(c.values()), c["newer, caught"] + c["newer, missed"]
            out.append(f"| {key} | {total:,} | {_pct(new, total)} | {_pct(c['newer, missed'], total)} "
                       f"| {_pct(c['newer, missed'], new)} |")
        return out + [""]

    lines += table("Road type", result.by_group, GROUPS)
    lines += table("What the map shows", result.by_state, STATES)
    lines += table("Age on the map", result.by_age, AGE_LABELS)
    changes = sum(result.sentinel_changes.values())
    lines += [f"**Sentinel changes:** {changes:,} of {result.sentinels:,} sentinels "
              f"({_pct(changes, result.sentinels)}) differ from the map. What they changed to:", "",
              "| change | sentinels | share |", "|---|---:|---:|"]
    for kind, count in result.sentinel_changes.most_common():
        lines.append(f"| {kind} | {count:,} | {_pct(count, changes)} |")
    return "\n".join(lines) + "\n"
