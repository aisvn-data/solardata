"""Reading a sheet, and measuring how wide its primary column block is.

Two jobs, and deliberately no more:

1.  Read the rows of a sheet's primary block, with a cache, because reading a
    sheet is 86% of the whole build.  0.8 parsed each file three times -- once
    to scan it, once in ``detect_block`` and once in ``iter_cells`` -- which is
    1,820 reads for 364 files.  The cache is the whole reason a build is about
    a minute rather than three.

2.  Report the block's **width**, and nothing else.  The width is the only
    thing the ingest needs from the reader: ``etl.catalog`` maps
    ``(station_id, width)`` to a layout, so a headerless file's column meaning
    comes from the catalog rather than from whichever sibling happened to have a
    header first.

What this reader deliberately does not do any more
---------------------------------------------------
0.8 resolved a headerless file's schema by finding the nearest *preceding*
sibling with a header and of exactly the same width, ordering on the parsed
timestamp.  That was 150 lines, it needed ``parse_local`` at the point of
selection rather than at the point of reading, and getting it wrong discarded
90% of the archive's measurements while still ingesting every timestamp.  The
archive contains exactly nine ``(station, width)`` pairs and no station has two
layouts of the same width, so the catalog's lookup is total and the failure mode
is a loud error instead of a silent one.  ``tests/test_ingest.py`` asserts both
halves of that.

Side blocks
-----------
Many sheets carry two or three parallel copies of the same readings to the right
of the primary block, separated by an empty column.  They are Google Sheets
formula experiments with mostly-zero duplicates, plus -- in ``Voltage_phumy`` --
a hand-made summary table at a coarser time granularity and the lab's own
annotations.  ``extra_blocks`` counts them so the report can show it, and
``iter_all_cells`` exists only to recover their prose into the notes table.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from openpyxl import load_workbook

from .times import looks_like_header, looks_like_timestamp

__all__ = [
    "SheetBlock",
    "cached_read_count",
    "clear_read_cache",
    "detect_block",
    "file_digest",
    "iter_all_cells",
    "iter_cells",
]

#: 32 is the widest row in the archive plus headroom. A sheet is never read past
#: this, which is what keeps a stray styled-but-empty column from turning into
#: 16,384 cells per row.
MAX_COLUMNS = 32

#: A column counts towards the block width only if at least this fraction of
#: body rows have something in it. Without it, one stray cell in column Z
#: redefines the layout of the file.
COLUMN_POPULARITY_THRESHOLD = 0.5

_TIME_HEADERS = frozenset({"time", "date", "timestamp", "datetime"})

# Keyed on (resolved path, size) so a file rewritten in place is not served from
# the cache. The tests write a fixture, ingest it, then write it again, and a
# path-only key would hand back the first version's rows.
_read_cache: dict[tuple[Path, int], tuple[list[list[str]], bool, int]] = {}


def _cell(cell: object) -> str:
    """One cell, as text.

    Two things this has to get right.

    A float that is integral becomes its integer spelling, because openpyxl hands
    back 342.0 for a cell that reads "342", and the sentinel table is keyed by
    float.  Normalising here is what makes -992 match -992.0.

    In ``read_only=True`` mode ``iter_rows()`` yields ``ReadOnlyCell`` and
    ``EmptyCell`` *objects*, not values, so the ``.value`` has to be read
    explicitly.  Passing the object straight to ``str()`` gives
    ``"<ReadOnlyCell 'Sheet1'.A1>"`` for every cell, which then looks like a
    populated column to the width detection and makes every headerless file
    13 columns wide.  ``EmptyCell`` is a ``ReadOnlyCell`` whose value is None, so
    one ``getattr`` covers both.
    """
    value = getattr(cell, "value", cell)
    if value is None:
        return ""
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else str(value)
    return str(value).strip()


def clear_read_cache() -> None:
    """Drop every parsed sheet. Called between archive folders and in tests."""
    _read_cache.clear()


def cached_read_count() -> int:
    """How many sheets are currently parsed and held. For tests and the report."""
    return len(_read_cache)


def _read_rows(path: Path) -> tuple[list[list[str]], bool, int]:
    """Every row of the first worksheet, as text, plus the header verdict.

    Cached, because reading a sheet is 86% of the whole build and 0.8 read each
    file three times -- once to scan it, once in ``detect_block`` and once in
    ``iter_cells``, which is 1,820 reads for 364 files.
    """
    key = (path.resolve(), path.stat().st_size)
    cached = _read_cache.get(key)
    if cached is not None:
        return cached

    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        sheet = workbook.worksheets[0]
        rows = [[_cell(c) for c in row[:MAX_COLUMNS]] for row in sheet.iter_rows()]
    finally:
        workbook.close()

    while rows and not any(rows[-1]):
        rows.pop()
    width = max((len(r) for r in rows), default=0)
    for row in rows:
        if len(row) < width:
            row.extend([""] * (width - len(row)))

    has_header = (
        bool(rows) and looks_like_header(rows[0][0]) and not looks_like_timestamp(rows[0][0])
    )

    result = (rows, has_header, width)
    _read_cache[key] = result
    return result


@dataclass
class SheetBlock:
    """What the ingest needs to know about a sheet before reading any of it."""

    path: Path
    n_columns: int
    has_header: bool
    n_rows: int
    extra_blocks: int = 0
    repeated_headers: list[int] = field(default_factory=list)


def detect_block(path: Path) -> SheetBlock:
    """Measure the primary column block.

    With a header row the block ends at the first empty header cell, because
    that is where the sheet author stopped naming columns.

    Without one, the block is the longest prefix of columns that at least half
    the body rows populate. That is the only inference left in the reader, and
    it is a measurement rather than a guess about meaning: the width it returns
    is looked up in the catalog, and an undeclared width fails the build.
    """
    rows, has_header, _width = _read_rows(path)
    if not rows:
        return SheetBlock(path=path, n_columns=1, has_header=False, n_rows=0)

    body = rows[1:] if has_header else rows
    header_cells = rows[0] if has_header else []
    n_cols = len(rows[0]) or 1

    if has_header:
        end = n_cols
        for i in range(1, len(header_cells)):
            if not header_cells[i]:
                end = i
                break
        n_cols = max(end, 1)

    if body:
        needed = max(1, int(len(body) * COLUMN_POPULARITY_THRESHOLD))
        populated = 0
        for i in range(1, n_cols):
            if sum(1 for r in body if i < len(r) and r[i]) >= needed:
                populated = i + 1
            else:
                break
        if not has_header:
            n_cols = max(populated, 1)

    extra = 0
    for row in rows:
        for i in range(n_cols, len(row)):
            if i < len(row) and looks_like_header(row[i]) and not looks_like_timestamp(row[i]):
                extra += 1
                break

    repeated: list[int] = []
    for offset, row in enumerate(body):
        if row and looks_like_header(row[0]) and not looks_like_timestamp(row[0]):
            repeated.append(offset + (2 if has_header else 1))

    return SheetBlock(
        path=path,
        n_columns=n_cols,
        has_header=has_header,
        n_rows=len(body),
        extra_blocks=extra,
        repeated_headers=repeated,
    )


def iter_cells(path: Path, block: SheetBlock | None = None) -> Iterator[tuple[int, list[str]]]:
    """Yield ``(sheet_row_number, cells)`` for the primary block only.

    ``sheet_row_number`` is 1-based as openpyxl reports it, so it is the number a
    person looking at the file would count, and it is what ``ROW_EXCLUSIONS`` and
    every rejects row refers to.
    """
    rows, has_header, _ = _read_rows(path)
    if block is None:
        block = detect_block(path)
    width = block.n_columns
    for offset, row in enumerate(rows):
        if has_header and offset == 0:
            continue
        yield offset + 1, row[:width]


def iter_all_cells(path: Path, block: SheetBlock | None = None) -> Iterator[tuple[int, int, str]]:
    """Yield ``(sheet_row, col_index, value)`` for the whole sheet.

    Exists for one purpose: the side blocks carry human prose -- the discharge
    summary and the annotations in ``Voltage_phumy`` -- and the notes table is
    where prose belongs. Column 0 is skipped because it is a timestamp or
    nothing.
    """
    rows, has_header, _ = _read_rows(path)
    if block is None:
        block = detect_block(path)
    for offset, row in enumerate(rows):
        if has_header and offset == 0:
            continue
        for index, value in enumerate(row):
            if index == 0 or not value:
                continue
            yield offset + 1, index, value


def file_digest(path: Path) -> str:
    """SHA-256 of the file, so a rebuild can prove it read the same bytes."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()
