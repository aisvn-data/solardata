#!/usr/bin/env python3
"""scripts/consolidate_raw.py

Consolidates multi-chunk raw XLSX files into single, clean XLSX files per station.
- Removes embedded charts, drawings, and extraneous XML.
- Preserves only the primary column block and clean data values.
- Adds an explicit row 1 header.
- Orders rows strictly chronologically by parsed timestamp.
- Deduplicates identical boundary rows.
- Moves legacy chunk files to data/archive/.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime
from pathlib import Path

# Add repo root to sys.path so we can import etl
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from etl.readers.times import looks_like_header, parse_local  # noqa: E402

NS = {"ns": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def col_letter(col_idx: int) -> str:
    """1-based column index to spreadsheet column letters."""
    result = ""
    while col_idx > 0:
        col_idx, remainder = divmod(col_idx - 1, 26)
        result = chr(65 + remainder) + result
    return result


def inspect_raw_chunk(path: Path):
    """Read a raw XLSX chunk file. Returns (header, body_rows, first_timestamp, primary_width)."""
    with zipfile.ZipFile(path) as z:
        strings = []
        if "xl/sharedStrings.xml" in z.namelist():
            tree = ET.fromstring(z.read("xl/sharedStrings.xml"))
            for si in tree.findall(".//ns:si", NS):
                t = si.find("ns:t", NS)
                if t is not None and t.text:
                    strings.append(t.text)
                else:
                    t_parts = [r.text for r in si.findall(".//ns:t", NS) if r.text]
                    strings.append("".join(t_parts))

        sheet_tree = ET.fromstring(z.read("xl/worksheets/sheet1.xml"))
        row_elems = sheet_tree.findall(".//ns:row", NS)
        if not row_elems:
            return None, [], None, 0

        def parse_row(r_elem):
            cells = []
            for c in r_elem.findall("ns:c", NS):
                t = c.get("t")
                if t == "inlineStr":
                    is_elem = c.find("ns:is/ns:t", NS)
                    cells.append(is_elem.text if is_elem is not None else "")
                elif t == "s":
                    v = c.find("ns:v", NS)
                    val = v.text if v is not None else ""
                    if val and int(val) < len(strings):
                        cells.append(strings[int(val)])
                    else:
                        cells.append(val)
                else:
                    v = c.find("ns:v", NS)
                    cells.append(v.text if v is not None else "")
            return cells

        rows = [parse_row(r) for r in row_elems]
        while rows and not any(rows[-1]):
            rows.pop()

        if not rows:
            return None, [], None, 0

        first = rows[0]
        has_hdr = bool(first and first[0] and looks_like_header(first[0]))
        header = first if has_hdr else None
        body = rows[1:] if has_hdr else rows

        # Primary block ends at first empty cell in header (or non-empty count in row 0)
        primary_width = (
            next((i for i, v in enumerate(first) if not v), len(first))
            if has_hdr
            else len([c for c in first if c])
        )

        first_ts = None
        for r in body:
            if r and r[0] and not looks_like_header(r[0]):
                try:
                    first_ts = parse_local(r[0])
                    break
                except Exception:
                    pass

        return header, body, first_ts, primary_width


def write_clean_xlsx(output_path: Path, header: list[str], rows: list[list[str]]) -> None:
    """Stream clean rows into an XLSX file with no drawings or charts."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = output_path.with_suffix(".tmp.xlsx")

    with zipfile.ZipFile(temp_path, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr(
            "[Content_Types].xml",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
</Types>""",
        )
        z.writestr(
            "_rels/.rels",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>""",
        )
        z.writestr(
            "xl/workbook.xml",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets>
    <sheet name="Sheet1" sheetId="1" r:id="rId1"/>
  </sheets>
</workbook>""",
        )
        z.writestr(
            "xl/_rels/workbook.xml.rels",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
</Relationships>""",
        )

        with z.open("xl/worksheets/sheet1.xml", "w") as sheet_f:
            sheet_f.write(
                b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                b'<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">\n'
                b"<sheetData>\n"
            )

            # Write Header (row 1)
            hdr_cells = []
            for c_idx, h in enumerate(header, 1):
                ref = f"{col_letter(c_idx)}1"
                h_esc = str(h).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                hdr_cells.append(f'<c r="{ref}" t="inlineStr"><is><t>{h_esc}</t></is></c>')
            sheet_f.write(f'<row r="1">{"".join(hdr_cells)}</row>\n'.encode())

            # Write Data Rows
            for r_idx, row in enumerate(rows, 2):
                row_cells = []
                for c_idx, val in enumerate(row, 1):
                    if val is None or val == "":
                        continue
                    ref = f"{col_letter(c_idx)}{r_idx}"
                    s_val = str(val).strip()
                    try:
                        f_val = float(s_val)
                        if f_val.is_integer() and "." not in s_val:
                            row_cells.append(f'<c r="{ref}"><v>{int(f_val)}</v></c>')
                        else:
                            row_cells.append(f'<c r="{ref}"><v>{s_val}</v></c>')
                    except ValueError:
                        esc = s_val.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                        row_cells.append(f'<c r="{ref}" t="inlineStr"><is><t>{esc}</t></is></c>')

                sheet_f.write(f'<row r="{r_idx}">{"".join(row_cells)}</row>\n'.encode())

            sheet_f.write(b"</sheetData>\n</worksheet>")

    if output_path.exists():
        output_path.unlink()
    temp_path.rename(output_path)


STATION_SPECS = {
    "phumy2": {
        "source_folders": ["phumy2", "phumy2a", "phumy2b"],
        "target_filename": "phumy2.xlsx",
        "header": ["time", "solar2", "current2", "power", "temp", "LiPo2", "boot"],
        "n_columns": 7,
    },
    "aisvn2": {
        "source_folders": ["aisvn2"],
        "target_filename": "aisvn2.xlsx",
        "header": [
            "time",
            "solar3",
            "battery2",
            "currentA",
            "currentB",
            "LiPo2",
            "load",
            "boot",
        ],
        "n_columns": 8,
    },
    "aisvn": {
        "source_folders": ["aisvn"],
        "target_filename": "aisvn.xlsx",
        "header": [
            "time",
            "solar",
            "battery",
            "current",
            "power",
            "load",
            "wind",
            "temp",
            "solar2",
            "LiPo",
            "boot",
        ],
        "n_columns": 11,
    },
    "AISVN_Solar": {
        "source_folders": ["AISVN_Solar"],
        "target_filename": "AISVN_Solar.xlsx",
        "header": [
            "time",
            "solar",
            "battery",
            "load_1",
            "load_2",
            "LiPo",
            "wind",
            "dump",
            "boot",
        ],
        "n_columns": 9,
    },
    "Solar_2020-05-16": {
        "source_folders": ["Solar_2020-05-16"],
        "target_filename": "Solar_2020-05-16.xlsx",
        "header": ["time", "event", "digital", "voltage", "LiPo"],
        "n_columns": 5,
    },
    "test": {
        "source_folders": ["test"],
        "exclude_files": ["IFTTT_test (1).xlsx"],  # Exclude the 11-column solar setup
        "target_filename": "test.xlsx",
        "header": ["time", "nix", "temp_c", "wifi_tx_ms"],
        "n_columns": 4,
    },
    "Maker_Webhooks_Events": {
        "source_folders": ["Maker_Webhooks_Events"],
        "target_filename": "Maker_Webhooks_Events.xlsx",
        "header": [
            "time",
            "solar",
            "battery",
            "curA",
            "curB",
            "load",
            "wind",
            "dump",
            "solar2",
            "LiPo",
            "boot",
        ],
        "n_columns": 11,
    },
}


def clean_voltage_phumy(raw_dir: Path, archive_dir: Path) -> None:
    """Clean the user-supplied Voltage_phumy.xlsx by stripping embedded drawings/charts and archiving old folder."""
    vp_file = raw_dir / "Voltage_phumy.xlsx"
    if not vp_file.exists():
        return

    print("\n==========================================")
    print("Processing Voltage_phumy.xlsx...")

    # Remove Windows ADS zone identifier if present
    zone_id = raw_dir / "Voltage_phumy.xlsx:Zone.Identifier"
    if zone_id.exists():
        zone_id.unlink()

    # Read rows and re-write with clean writer (stripping drawing1.xml)
    hdr, body, _first_ts, _width = inspect_raw_chunk(vp_file)
    print(f"  Header: {hdr}")
    print(f"  Total data rows: {len(body)}")
    write_clean_xlsx(vp_file, hdr, body)
    print("  Re-wrote clean Voltage_phumy.xlsx (stripped charts/drawings).")

    # Move old folder data/raw/Voltage_phumy to data/archive/Voltage_phumy
    old_folder = raw_dir / "Voltage_phumy"
    if old_folder.exists() and old_folder.is_dir():
        dst = archive_dir / "Voltage_phumy"
        print(f"  Archiving {old_folder} -> {dst}")
        archive_dir.mkdir(parents=True, exist_ok=True)
        res = subprocess.run(
            ["git", "mv", str(old_folder), str(dst)], capture_output=True, text=True
        )
        if res.returncode != 0:
            if dst.exists():
                shutil.rmtree(dst)
            shutil.move(old_folder, dst)


def consolidate_station(
    station_key: str, raw_dir: Path, archive_dir: Path, dry_run: bool = False
) -> None:
    spec = STATION_SPECS[station_key]
    print("\n==========================================")
    print(f"Consolidating station: {station_key}")
    print(f"Source folders: {spec['source_folders']}")
    print(f"Header ({spec['n_columns']} cols): {spec['header']}")
    exclude_files = set(spec.get("exclude_files", []))

    all_chunks = []
    for folder_name in spec["source_folders"]:
        f_dir = raw_dir / folder_name
        if not f_dir.exists():
            f_dir = archive_dir / folder_name
            if not f_dir.exists():
                print(f"  Warning: folder {folder_name} not found in raw or archive!")
                continue
        for p in sorted(f_dir.glob("*.xlsx")):
            if p.name.startswith("~$") or p.name.endswith(".tmp.xlsx"):
                continue
            if p.name in exclude_files:
                print(f"  Excluding file: {p.name}")
                continue
            all_chunks.append(p)

    print(f"  Found {len(all_chunks)} raw chunk files to process.")
    if not all_chunks:
        return

    parsed_chunks = []
    for p in all_chunks:
        hdr, body, first_ts, primary_w = inspect_raw_chunk(p)
        if not body:
            continue
        parsed_chunks.append(
            {
                "path": p,
                "header": hdr,
                "body": body,
                "first_ts": first_ts,
                "primary_width": primary_w,
            }
        )

    parsed_chunks.sort(key=lambda c: c["first_ts"] or datetime.max)
    print(f"  First chunk: {parsed_chunks[0]['path'].name} ({parsed_chunks[0]['first_ts']})")
    print(f"  Last chunk:  {parsed_chunks[-1]['path'].name} ({parsed_chunks[-1]['first_ts']})")

    n_cols = spec["n_columns"]
    combined_rows = []
    seen_timestamps = set()
    n_boundary_dups = 0

    for chunk in parsed_chunks:
        is_maker_10 = station_key == "Maker_Webhooks_Events" and chunk["primary_width"] == 10
        for r in chunk["body"]:
            if not r or not r[0] or looks_like_header(r[0]):
                continue

            ts = r[0].strip()
            if ts in seen_timestamps:
                n_boundary_dups += 1
                continue
            seen_timestamps.add(ts)

            if is_maker_10:
                # 10 cols: r[:8] (time..dump) + [''] (blank solar2) + r[8:10] (LiPo, boot)
                clean_r = [*r[:8], "", *r[8:10]]
            else:
                clean_r = r[:n_cols]
                if len(clean_r) < n_cols:
                    clean_r.extend([""] * (n_cols - len(clean_r)))

            combined_rows.append(clean_r)

    print(
        f"  Total unique data rows: {len(combined_rows)} (absorbed {n_boundary_dups} duplicate boundary rows)"
    )

    if dry_run:
        print("  [DRY RUN] Skipping file writing.")
        return

    target_file = raw_dir / spec["target_filename"]
    print(f"  Writing consolidated file to: {target_file}")
    t0 = time.time()
    write_clean_xlsx(target_file, spec["header"], combined_rows)
    print(
        f"  Wrote {target_file.name} in {time.time() - t0:.2f}s ({target_file.stat().st_size} bytes)"
    )

    with zipfile.ZipFile(target_file) as z:
        charts = [n for n in z.namelist() if "drawing" in n.lower() or "chart" in n.lower()]
        if charts:
            print(f"  ERROR: Embedded drawings still found: {charts}")
        else:
            print("  Verified: 0 drawings/charts in consolidated file.")


def archive_source_folders(raw_dir: Path, archive_dir: Path, folders: list[str]) -> None:
    archive_dir.mkdir(parents=True, exist_ok=True)
    for folder_name in folders:
        src = raw_dir / folder_name
        dst = archive_dir / folder_name
        if src.exists() and src.is_dir():
            print(f"Moving {src} -> {dst}")
            res = subprocess.run(["git", "mv", str(src), str(dst)], capture_output=True, text=True)
            if res.returncode != 0:
                if dst.exists():
                    shutil.rmtree(dst)
                shutil.move(src, dst)


def main():
    parser = argparse.ArgumentParser(description="Consolidate raw XLSX files per station.")
    parser.add_argument(
        "--station",
        choices=[*STATION_SPECS.keys(), "all", "voltage_phumy"],
        default="all",
        help="Station to consolidate",
    )
    parser.add_argument("--dry-run", action="store_true", help="Inspect and report without writing")
    parser.add_argument(
        "--no-archive",
        action="store_true",
        help="Do not move source folders to data/archive/",
    )
    args = parser.parse_args()

    raw_dir = REPO_ROOT / "data" / "raw"
    archive_dir = REPO_ROOT / "data" / "archive"

    if args.station == "voltage_phumy":
        clean_voltage_phumy(raw_dir, archive_dir)
        return

    stations_to_process = list(STATION_SPECS.keys()) if args.station == "all" else [args.station]

    for st in stations_to_process:
        consolidate_station(st, raw_dir, archive_dir, dry_run=args.dry_run)

    clean_voltage_phumy(raw_dir, archive_dir)

    if not args.dry_run and not args.no_archive:
        print("\n==========================================")
        print("Archiving legacy chunk folders to data/archive/...")
        folders_to_archive = set()
        for st in stations_to_process:
            for f in STATION_SPECS[st]["source_folders"]:
                folders_to_archive.add(f)
        archive_source_folders(raw_dir, archive_dir, sorted(folders_to_archive))
        print("Archiving complete.")


if __name__ == "__main__":
    main()
