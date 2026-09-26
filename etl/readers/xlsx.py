"""Reading the Google Sheets XLSX exports.

Three structural problems have to be solved before any value can be trusted,
and all three are handled here rather than in the callers:

1. **Header presence.**  Only 36 of the 364 files carry a header row.  The rest
   start straight into data, so a naive ``pandas.read_excel`` produces a data
   row masquerading as a header for 90% of the archive.

2. **Side-by-side column blocks.**  Many sheets contain two or three parallel
   blocks of the same observations, separated by an empty column -- typically
   Google Sheets formula experiments.  ``Voltage_phumy.xlsx`` has three, one of
   which is a hand-made summary table at a different time granularity.  Reading
   ``A:Z`` blindly interleaves them.

3. **Mid-file header rows.**  A few files repeat a header row partway down,
   where a new block of readings was appended.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import openpyxl

from etl.readers.times import looks_like_header, looks_like_timestamp

#: Read enough columns to see every block we know about, but not unbounded.
MAX_COLUMNS = 32

#: A column counts towards the primary block if it is populated in at least this
#: fraction of body rows.
COLUMN_POPULARITY_THRESHOLD = 0.5


@dataclass
class SheetBlock:
    """The first contiguous block of columns in a sheet, starting at column A."""

    path: Path
    sheet_name: str
    header: tuple[str, ...] | None
    n_columns: int
    #: Total number of rows in the sheet, excluding a fully blank tail.
    n_rows: int
    #: How many additional ``time``-headed blocks were found to the right.
    extra_blocks: int = 0
    #: Rows that looked like a repeated header inside the data body.
    repeated_headers: list[int] = field(default_factory=list)
    #: Sheet has more than one worksheet.
    multi_sheet: bool = False


def _cell(value) -> str:
    """Normalise an openpyxl cell to a trimmed string ('' for empty)."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


#: Parsed-sheet cache, keyed by resolved path.
#:
#: The ingest reads every file three times: once to scan it for its structure,
#: once more in ``detect_block``, and once more in ``iter_cells``. Reading a
#: sheet is ~86% of the whole build, so that redundancy dominates the runtime --
#: profiling 364 files showed 1820 calls to ``_read_rows`` for 364 files.
#:
#: The cache is keyed by path *and* size so a file edited mid-run cannot be
#: served stale, and it is cleared between archive folders by
#: :func:`etl.build_db.ingest` to bound memory to one folder.
_read_cache: dict[Path, tuple[str, list[list[str]], bool, int]] = {}


def clear_read_cache() -> None:
    """Drop every cached sheet. Called between archive folders."""
    _read_cache.clear()


def _read_rows(path: Path) -> tuple[str, list[list[str]], bool]:
    """Return ``(sheet_name, rows, multi_sheet)`` for the first worksheet.

    Two normalisations happen here and both are load-bearing:

    * ``_cell`` renders every float that happens to be integral as an integer
      string, so 2000.0 reads back as ``2000``.  Numerically identical, but it
      keeps the stored value, the Parquet output and the CSV exports free of
      spurious ``.0`` tails.
    * The sheet is read in full, then the blank tail is trimmed.  Google Sheets
      exports declare no dimension, so openpyxl reports the worksheet as
      "unsized" and callers must never call ``calculate_dimension()`` on it.

    Served from the cache when possible; see ``_read_cache``.
    """
    key = path.resolve()
    size = key.stat().st_size
    cached = _read_cache.get(key)
    if cached is not None and cached[3] == size:
        return cached[0], cached[1], cached[2]

    workbook = openpyxl.load_workbook(key, read_only=True, data_only=True)
    try:
        name = workbook.sheetnames[0]
        sheet = workbook[name]
        raw = [
            [_cell(v) for v in row]
            for row in sheet.iter_rows(max_col=MAX_COLUMNS, values_only=True)
        ]
        multi = len(workbook.sheetnames) > 1
    finally:
        workbook.close()
    # Trim fully blank rows from the tail (Sheets pads exports).
    while raw and not any(raw[-1]):
        raw.pop()
    # Trim fully blank columns from the tail of every row.
    width = 0
    for row in raw:
        for i, value in enumerate(row):
            if value:
                width = max(width, i + 1)
    rows = [row[:width] for row in raw]
    _read_cache[key] = (name, rows, multi, size)
    return name, rows, multi


def detect_block(path: Path) -> SheetBlock:
    """Inspect a file and describe its primary column block.

    The primary block is the run of columns starting at A that ends at the first
    fully empty column (when a header is available) or at the first sparsely
    populated column (when it is not).  Anything beyond that is a separate block
    and is deliberately excluded -- in the archive those are Google Sheets
    formula experiments duplicating the same readings with different (mostly
    zero) values, plus, in ``Voltage_phumy``, a hand-made summary table at a
    coarser time granularity and the discharge-test annotations.
    """
    sheet_name, rows, multi_sheet = _read_rows(path)
    if not rows:
        return SheetBlock(path, sheet_name, None, 0, 0, multi_sheet=multi_sheet)

    first = rows[0]
    has_header = bool(first) and first[0].lower() in ("time", "date", "timestamp")
    body = rows[1:] if has_header else rows

    if has_header:
        # The header names the columns, so the block ends at the first empty
        # header cell -- which is also the separator from any side block.
        end = next((i for i, v in enumerate(first) if not v), len(first))
    else:
        # No header to read, so infer the boundary from how consistently each
        # column is filled.  The block is the longest prefix of columns populated
        # on at least half the body rows.  Side-block columns are sparse -- a
        # hand-written annotation appears on one row out of two thousand -- so
        # this separates them without a header to guide us.
        width = max((len(row) for row in body), default=1)
        total = max(len(body), 1)
        end = 1
        for index in range(1, width):
            hits = sum(1 for row in body if index < len(row) and row[index])
            if hits / total < COLUMN_POPULARITY_THRESHOLD:
                break
            end = index + 1

    end = max(end, 1)
    extra = sum(1 for v in first[end:] if v.lower() in ("time", "date", "timestamp", "datetime"))

    repeated = [
        i + 1
        for i, row in enumerate(body)
        if row and row[0] and row[0].lower() in ("time", "date", "timestamp", "datetime")
    ]

    return SheetBlock(
        path=path,
        sheet_name=sheet_name,
        header=tuple(first[:end]) if has_header else None,
        n_columns=end,
        n_rows=len(body),
        extra_blocks=extra,
        repeated_headers=repeated,
        multi_sheet=multi_sheet,
    )


def iter_cells(path: Path, block: SheetBlock | None = None) -> Iterator[tuple[int, list[str]]]:
    """Yield ``(excel_row_number, cells)`` for the primary block only.

    Row numbers are 1-based and refer to the original sheet, so they can be
    cited in the ``rejects`` table and traced back to the raw file.
    """
    if block is None:
        block = detect_block(path)
    sheet_name, rows, _ = _read_rows(path)
    del sheet_name
    skip = 1 if block.header is not None else 0
    for index in range(skip, len(rows)):
        yield index + 1, rows[index][: block.n_columns]


def header_of(path: Path) -> tuple[str, ...] | None:
    return detect_block(path).header


def file_digest(path: Path) -> str:
    """SHA-256 of the raw file, so a rebuild can prove it used the same input.

    The raw archive is the only copy of this data, so the build records what it
    read.  If `data/raw` is ever re-exported from Google Sheets, these digests
    are what make the difference detectable.
    """
    import hashlib

    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def looks_like_data_row(cells: list[str]) -> bool:
    return (
        bool(cells)
        and bool(cells[0])
        and (looks_like_timestamp(cells[0]) or not looks_like_header(cells[0]))
    )


def iter_all_cells(path: Path, block: SheetBlock | None = None) -> Iterator[tuple[int, int, str]]:
    """Yield ``(sheet_row, col_index, value)`` for the **whole** sheet.

    Used to recover human notes, which are written in whatever spare column was
    free and therefore usually sit outside the primary block -- for example the
    discharge-test annotations in ``data/raw/Voltage_phumy``.

    Column 0 is skipped because it holds the observation timestamps, and the
    header row is skipped so that side-block header words such as ``time`` are
    not mistaken for annotations.
    """
    if block is None:
        block = detect_block(path)
    _, rows, _ = _read_rows(path)
    start = 2 if block.header else 1
    for row_index in range(start - 1, len(rows)):
        row = rows[row_index]
        for col_index, value in enumerate(row):
            if col_index == 0 or not value:
                continue
            yield row_index + 1, col_index, value
