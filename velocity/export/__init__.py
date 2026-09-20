"""Export layer — the pipeline's frames as Excel-ready CSV.

The hybrid architecture (docs/HYBRID_MIGRATION_PLAN.md) puts Excel in front
of Velocity: the engine simulates, this package writes flat files, and Power
Query reads them. Every module here is a **read-only projection** of frames
the pipeline already banks — no fitting, no simulating, no re-pricing and no
re-staking. A number that reaches a CSV came from the run that produced it.

The contract Excel depends on, and the tests pin:

* **Stable filenames.** ``datasets/exports/games.csv``, never a stamped name;
  a Power Query source that changes name every run is not a source.
* **Stable column order.** Each module owns a ``*_COLUMNS`` tuple and writes
  exactly those columns in exactly that order.
* **A file always exists.** With no data the exporter writes the header row
  alone rather than nothing, so a refresh finds a table with zero rows
  instead of a broken query.
* **Metadata on every row.** ``generated_at``, ``season``, ``week``.
* **Nothing is guessed.** A column with no honest source in the banked frames
  is written empty.
"""

from velocity.export.meta import EXPORT_DIR, ExportMeta, write_csv

__all__ = ["EXPORT_DIR", "ExportMeta", "write_csv"]
