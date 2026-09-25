"""Value-level normalisation: metric mapping, quality flags, unit regimes."""

from etl.normalize.metrics import (  # noqa: F401
    CANONICAL_COLUMNS,
    METRIC_BY_COLUMN,
    METRICS,
    NUMERIC_COLUMNS,
    TEXT_COLUMNS,
    HeaderMapping,
    Metric,
    build_row_mapping,
    map_header,
    map_headers,
)
from etl.normalize.quality import (  # noqa: F401
    CellResult,
    clip_run_indices,
    coerce_cell,
    coerce_number,
    merge_flags,
)
from etl.normalize.units import Regime, detect_per_file, propose_regime  # noqa: F401
