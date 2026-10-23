"""Split the UK into shards that each take about an hour to sample.

A shard is a set of 1-degree cells; a way belongs to the cell holding its middle
vertex (measure.segment_cell), so every way is in exactly one shard. The table was
packed from the 2026-10 `measure` run so each shard needs 1.0-1.7M lookups at
minimum, well inside GitHub Actions' 6-hour job limit at 400 lookups/s.
"""

from typing import Dict, List

SHARD_CELLS: Dict[str, List[str]] = {
    "uk-1": ["49N 7W", "49N 6W", "50N 6W", "50N 5W", "50N 4W", "50N 3W", "50N 2W", "50N 1W", "50N 0E",
             "50N 1E", "51N 6W", "51N 5W"],
    "uk-2": ["51N 4W", "51N 3W", "51N 2W"],
    "uk-3": ["51N 1W"],
    "uk-4": ["51N 0E", "51N 1E", "52N 6W", "52N 5W", "52N 4W"],
    "uk-5": ["52N 3W", "52N 2W"],
    "uk-6": ["52N 1W", "52N 0E", "52N 1E", "53N 5W", "53N 4W"],
    "uk-7": ["53N 3W"],
    "uk-8": ["53N 2W", "53N 1W", "53N 0E", "54N 9W", "54N 8W", "54N 7W", "54N 6W"],
    "uk-9": ["54N 5W", "54N 4W", "54N 3W", "54N 2W", "54N 1W", "55N 8W", "55N 7W", "55N 6W", "55N 5W"],
    "uk-10": ["55N 4W", "55N 3W", "55N 2W", "56N 8W", "56N 7W", "56N 6W", "56N 5W", "56N 4W", "56N 3W",
             "57N 9W", "57N 8W", "57N 7W", "57N 6W", "57N 5W", "57N 4W", "57N 3W", "57N 2W", "58N 8W",
             "58N 7W", "58N 6W", "58N 5W", "58N 4W", "58N 3W", "59N 6W", "59N 5W", "59N 4W", "59N 3W",
             "59N 2W", "60N 3W", "60N 2W", "60N 1W"],
}

SHARDS = list(SHARD_CELLS)

# Cells the measurement didn't see (new roads on a remote island, say) go here
FALLBACK_SHARD = SHARDS[-1]

_SHARD_OF = {cell: shard for shard, cells in SHARD_CELLS.items() for cell in cells}


def shard_of(cell: str) -> str:
    return _SHARD_OF.get(cell, FALLBACK_SHARD)
