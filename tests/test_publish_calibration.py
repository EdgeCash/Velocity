"""Publish-gate calibration — the floor sweep over banked audit frames.

Pins the sweep's contract: it re-applies exactly the gate's threshold-
independent rules (publishable tier, the edge band, no adverse drift) before
counting, honors the nightly cap by conviction, and is monotone in both
floors — so the table an operator reads maps one-to-one onto what the live
gate would have posted at those constants.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
from velocity.intel.calibrate import load_audits, rejection_summary, sweep_floors

_NIGHT1 = pd.Timestamp("2026-09-10 22:00")
_NIGHT2 = pd.Timestamp("2026-09-11 22:00")


def _row(conviction: float, context: float, *, tier: str = "A", edge: float = 0.05,
         drift: float | None = None, generated_at: pd.Timestamp = _NIGHT1,
         published: bool = False, reason: str = "gated") -> dict:
    return {
        "game_id": "g", "market": "total", "side": "over", "player": None,
        "price": -110.0, "stake": 1.0, "edge": edge, "tier": tier, "drift": drift,
        "conviction": conviction, "context": context, "published": published,
        "reason": "" if published else reason, "generated_at": generated_at,
    }


def _audit() -> pd.DataFrame:
    night1 = [
        _row(c, 0.10, published=c >= 0.72)
        for c in (0.90, 0.85, 0.80, 0.75, 0.70, 0.67, 0.66)
    ]
    night2 = [
        # Vetoed and out-of-band candidates never count, whatever their floors.
        _row(0.99, 0.90, tier="X", generated_at=_NIGHT2, reason="vetoed by the intel layer"),
        _row(0.95, 0.50, edge=0.20, generated_at=_NIGHT2, reason="edge above ceiling"),
        # Clears the conviction floor but not the context floor …
        _row(0.74, 0.02, generated_at=_NIGHT2, reason="context does not corroborate"),
        # … and the reverse.
        _row(0.66, 0.30, generated_at=_NIGHT2, reason="conviction below floor"),
    ]
    frame = pd.DataFrame(night1 + night2)
    frame["night"] = pd.to_datetime(frame["generated_at"]).dt.date
    return frame


def test_sweep_counts_match_the_gate_rules_exactly() -> None:
    table = sweep_floors(_audit(), conviction_grid=(0.65, 0.72),
                         context_grid=(0.0, 0.05), max_plays=5)
    by_pair = {(r["min_conviction"], r["min_context"]): r
               for r in table.to_dict("records")}
    # Floors at "tier A alone": night 1's seven candidates cap at five, night
    # 2 keeps only the two in-band tier-A rows (the veto and the edge above
    # the ceiling are out at every floor).
    assert by_pair[(0.65, 0.0)]["plays"] == 5 + 2
    # The context floor drops night 2's 0.02-context row.
    assert by_pair[(0.65, 0.05)]["plays"] == 5 + 1
    # The current conviction floor: four clear it on night 1, one on night 2.
    assert by_pair[(0.72, 0.0)]["plays"] == 4 + 1
    # Both floors: night 2 goes dark — "no picks is a pick", measured.
    row = by_pair[(0.72, 0.05)]
    assert row["plays"] == 4
    assert row["empty_nights"] == 0.5
    assert row["nights"] == 2
    # Tightening a floor never adds volume.
    assert (
        by_pair[(0.65, 0.0)]["plays"] >= by_pair[(0.65, 0.05)]["plays"]
        >= by_pair[(0.72, 0.05)]["plays"]
    )


def test_nightly_cap_keeps_the_highest_conviction_plays() -> None:
    table = sweep_floors(_audit(), conviction_grid=(0.65,), context_grid=(0.0,),
                         max_plays=2)
    assert int(table.iloc[0]["plays"]) == 2 + 2  # capped per night, not overall


def test_load_audits_stacks_files_and_stamps_nights(tmp_path: Path) -> None:
    frame = _audit().drop(columns=["night"])
    frame.iloc[:7].to_parquet(tmp_path / "publish_nfl_a.parquet", index=False)
    frame.iloc[7:].to_parquet(tmp_path / "publish_nfl_b.parquet", index=False)
    (tmp_path / "slate_nfl_a.parquet").write_bytes(b"")  # never globbed

    audit = load_audits(tmp_path)
    assert len(audit) == 11
    assert set(audit["source_file"]) == {"publish_nfl_a.parquet", "publish_nfl_b.parquet"}
    assert audit["night"].nunique() == 2

    assert load_audits(tmp_path / "empty").empty  # no files → empty, not a crash


def test_rejection_summary_counts_reasons() -> None:
    summary = rejection_summary(_audit())
    counts = dict(zip(summary["reason"], summary["n"], strict=True))
    assert counts["gated"] == 3  # night 1's misses
    assert int(summary["n"].sum()) == 7  # every unpublished row lands somewhere
