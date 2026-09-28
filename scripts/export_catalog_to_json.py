"""Export etl/catalog.py definitions into data/config/normalization.json and data/config/curation.json."""

from __future__ import annotations

import json
from pathlib import Path

from etl import catalog

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = REPO_ROOT / "data" / "config"


def export():
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    normalization = {"stations": {}}
    curation = {"stations": {}}

    for station in catalog.STATIONS:
        st_id = station.station_id

        # 1. Normalization data
        norm_layouts = []
        for layout in station.layouts:
            norm_layouts.append(
                {
                    "n_columns": layout.n_columns,
                    "header": list(layout.header),
                    "channels": list(layout.channels),
                    "n_files": layout.n_files,
                    "note": layout.note,
                }
            )

        norm_channels = {}
        cur_channels = {}

        for ch in station.channels:
            norm_corrs = []
            for corr in ch.corrections:
                norm_corrs.append(
                    {
                        "from_ts": corr.from_ts,
                        "to_ts": corr.to_ts,
                        "op": corr.op,
                        "value": corr.value,
                        "note": corr.note,
                    }
                )

            norm_channels[ch.name] = {
                "name": ch.name,
                "kind": ch.kind,
                "raw_unit": ch.raw_unit,
                "unit": ch.unit,
                "scale": ch.scale,
                "counter": ch.counter,
                "corrections": norm_corrs,
            }

            cur_channels[ch.name] = {
                "label": ch.label,
                "description": ch.description,
                "band": list(ch.band) if ch.band else None,
                "band_note": ch.band_note,
                "publish": ch.publish,
                "exclude": ch.exclude,
                "exclude_note": ch.exclude_note,
                "stats": list(ch.stats),
            }

        normalization["stations"][st_id] = {
            "station_id": st_id,
            "display_name": station.display_name,
            "location": station.location,
            "tz": station.tz,
            "source_dirs": list(station.source_dirs),
            "applet": station.applet,
            "production": station.production,
            "published_group": station.published_group,
            "notes": station.notes,
            "layouts": norm_layouts,
            "channels": norm_channels,
        }

        curation["stations"][st_id] = {
            "open_questions": list(station.open_questions),
            "channels": cur_channels,
        }

    norm_path = CONFIG_DIR / "normalization.json"
    cur_path = CONFIG_DIR / "curation.json"

    with open(norm_path, "w", encoding="utf-8") as f:
        json.dump(normalization, f, indent=2, ensure_ascii=False)
        f.write("\n")

    with open(cur_path, "w", encoding="utf-8") as f:
        json.dump(curation, f, indent=2, ensure_ascii=False)
        f.write("\n")

    print(f"Exported {len(catalog.STATIONS)} stations to {norm_path} and {cur_path}")


if __name__ == "__main__":
    export()
