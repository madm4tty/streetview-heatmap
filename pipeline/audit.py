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
nothing newer. Lookups go to a throwaway cache, and the pipeline's own cache
and results are only read.

A point can differ from the map without anything new: the map dates a stretch
from lookups at its ends, so its middle can give another panorama. To count
only new imagery, each point is kept (`points`), and `recheck` looks the same
points up again later, after a build: a point whose answer is now newer than
at the baseline has new imagery, and the map either has it by then or the
refresh missed it. Differences that were already there cancel out.
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


def _tables() -> Dict[str, Dict[str, Counter]]:
    return {name: defaultdict(Counter) for name in ("by_group", "by_state", "by_age", "by_year")}


@dataclass
class AuditResult:
    probes: int = 0
    outcomes: Counter = field(default_factory=Counter)        # same, newer caught, newer missed, other
    tables: Dict[str, Dict[str, Counter]] = field(default_factory=_tables)
    sentinels: int = 0
    sentinel_changes: Counter = field(default_factory=Counter)
    lookups: int = 0
    # Each point, to look up again later: road, position, length, key, map's value, Google's
    points: List[Dict] = field(default_factory=list)

    @property
    def by_group(self):
        return self.tables["by_group"]

    @property
    def by_state(self):
        return self.tables["by_state"]

    @property
    def by_age(self):
        return self.tables["by_age"]

    def add(self, other: "AuditResult") -> "AuditResult":
        self.probes += other.probes
        self.outcomes += other.outcomes
        _add_tables(self.tables, other.tables)
        self.sentinels += other.sentinels
        self.sentinel_changes += other.sentinel_changes
        self.lookups += other.lookups
        self.points += other.points
        return self

    def to_json(self) -> Dict:
        return {"probes": self.probes, "outcomes": dict(self.outcomes),
                **{name: {k: dict(v) for k, v in t.items()} for name, t in self.tables.items()},
                "sentinels": self.sentinels, "sentinel_changes": dict(self.sentinel_changes),
                "lookups": self.lookups}


def _add_tables(mine: Dict[str, Dict[str, Counter]], theirs: Dict[str, Dict[str, Counter]]) -> None:
    for name, table in theirs.items():
        for key, counts in table.items():
            mine[name][key] += counts


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
        dist = rng.uniform(piece.start_m + inset, piece.end_m - inset)
        chosen.append((seg, piece, key_at(seg, dist), dist))

    # Every sentinel a refresh would check for these roads, looked up with the probes
    cells_of = {seg.id: segment_cells(seg, checks.size) for seg, _, _, _ in chosen}
    cells = set().union(*cells_of.values()) & checks.sentinels.keys()
    keys = [k for _, _, k, _ in chosen] + [checks.sentinels[c].key for c in cells]
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
    for seg, piece, key, dist in chosen:
        outcome = compare(piece.value, fresh[key])
        if outcome == "newer":
            outcome = "newer, caught" if cells_of[seg.id] & changed else "newer, missed"
            # Recent captures are likely new; old ones are another existing panorama
            result.tables["by_year"][fresh[key][:4]][outcome] += 1
        result.probes += 1
        result.outcomes[outcome] += 1
        result.by_group[road_group(seg.highway)][outcome] += 1
        result.by_state[state_of(piece.value)][outcome] += 1
        if dated(piece.value):
            result.by_age[AGE_LABELS[band_of(max(0, now_index - month_index(piece.value)))]][outcome] += 1
        result.points.append({"seg": seg.id, "dist": round(dist, 1), "length": round(seg.length, 1),
                              "key": list(key), "map": piece.value, "google": fresh[key]})
    return result


@dataclass
class RecheckResult:
    """The same points looked up again: what's new since the baseline, and whether the map has it."""
    points: int = 0
    skipped: int = 0   # roads changed in OSM since, or gone
    outcomes: Counter = field(default_factory=Counter)
    unstable: int = 0  # Google's answer changed without being newer
    tables: Dict[str, Dict[str, Counter]] = field(default_factory=_tables)

    def add(self, other: "RecheckResult") -> "RecheckResult":
        self.points += other.points
        self.skipped += other.skipped
        self.outcomes += other.outcomes
        self.unstable += other.unstable
        _add_tables(self.tables, other.tables)
        return self

    def to_json(self) -> Dict:
        return {"points": self.points, "skipped": self.skipped, "outcomes": dict(self.outcomes),
                "unstable": self.unstable,
                **{name: {k: dict(v) for k, v in t.items()} for name, t in self.tables.items()}}


async def recheck(points: List[Dict], segments, pieces, resolve_fresh: Resolver) -> RecheckResult:
    """Look up a baseline's points again and compare with then, and with the map now."""
    by_id = {s.id: s for s in segments}
    by_seg: Dict[str, list] = defaultdict(list)
    for p in pieces:
        by_seg[p.segment_id].append(p)
    result, usable = RecheckResult(), []
    for point in points:
        seg = by_id.get(point["seg"])
        if seg is None or abs(seg.length - point["length"]) > 1.0:
            result.skipped += 1
            continue
        on_map = next((p.value for p in by_seg[seg.id] if p.start_m <= point["dist"] <= p.end_m), None)
        if on_map is None:
            result.skipped += 1
            continue
        usable.append((point, seg, on_map))
    fresh = await resolve_fresh(list(dict.fromkeys(tuple(pt["key"]) for pt, _, _ in usable)))
    for point, seg, on_map in usable:
        now = fresh[tuple(point["key"])]
        if compare(point["google"], now) != "newer":
            outcome = "no new imagery"
            result.unstable += now != point["google"]
        else:
            versus_map = compare(on_map, now)
            outcome = ("new, on the map" if versus_map == "same" else
                       "new, missed" if versus_map == "newer" else "new, map differs")
            result.tables["by_year"][now[:4]][outcome] += 1
        result.points += 1
        result.outcomes[outcome] += 1
        result.tables["by_group"][road_group(seg.highway)][outcome] += 1
        result.tables["by_state"][state_of(point["map"])][outcome] += 1
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
    years = result.tables["by_year"]
    if years:
        lines += ["Capture year of the newer imagery (new captures are recent; older ones are most likely "
                  "another existing panorama, not new imagery):", "",
                  "| year | points | missed |", "|---|---:|---:|"]
        for year in sorted(years, reverse=True):
            c = years[year]
            lines.append(f"| {year} | {sum(c.values()):,} | {c['newer, missed']:,} |")
        lines.append("")
    changes = sum(result.sentinel_changes.values())
    lines += [f"**Sentinel changes:** {changes:,} of {result.sentinels:,} sentinels "
              f"({_pct(changes, result.sentinels)}) differ from the map. What they changed to:", "",
              "| change | sentinels | share |", "|---|---:|---:|"]
    for kind, count in result.sentinel_changes.most_common():
        lines.append(f"| {kind} | {count:,} | {_pct(count, changes)} |")
    return "\n".join(lines) + "\n"


def recheck_markdown(result: RecheckResult, title: str) -> str:
    """The comparison with a baseline, as a Markdown report."""
    o, n = result.outcomes, result.points
    new = o["new, on the map"] + o["new, missed"] + o["new, map differs"]
    lines = [
        f"## {title}", "",
        f"{n:,} points looked up again ({result.skipped:,} skipped: their road changed in OSM since). "
        "Shares are shares of road length.", "",
        f"- **New imagery since the baseline:** {_pct(new, n)} of road (Google's answer at the same "
        "point is newer than it was)",
        f"  - on the map now: {_pct(o['new, on the map'], n)}; **missed by the refresh: "
        f"{_pct(o['new, missed'], n)}** (so it missed {_pct(o['new, missed'], new)} of the new imagery)",
        f"  - the map shows something else there: {_pct(o['new, map differs'], n)}",
        f"- Google's answer changed without being newer: {_pct(result.unstable, n)} (lookup noise)", "",
    ]
    for heading, name, order in (("Road type", "by_group", GROUPS),
                                 ("What the map showed", "by_state", STATES)):
        lines += [f"| {heading} | points | new | missed | missed of new |", "|---|---:|---:|---:|---:|"]
        for key in order:
            c = result.tables[name].get(key)
            if not c:
                continue
            total = sum(c.values())
            got = c["new, on the map"] + c["new, missed"] + c["new, map differs"]
            lines.append(f"| {key} | {total:,} | {_pct(got, total)} | {_pct(c['new, missed'], total)} "
                         f"| {_pct(c['new, missed'], got)} |")
        lines.append("")
    years = result.tables["by_year"]
    if years:
        lines += ["| capture year of the new imagery | points | missed |", "|---|---:|---:|"]
        for year in sorted(years, reverse=True):
            lines.append(f"| {year} | {sum(years[year].values()):,} | {years[year]['new, missed']:,} |")
    return "\n".join(lines) + "\n"
