"""Render the newest persisted slate as an email body + subject (for the workflow).

Thin glue over :func:`velocity.report.email_html.render_slate_email`: find the
newest run's parquets in the slate folder (game plays, props, parlays, and the
games map that turns provider ids into matchups), render, and write
``slate_email.html`` + ``subject.txt`` for the mail step to pick up. An empty or
missing slate still renders the "no plays today" heartbeat.

    python scripts/render_slate_email.py --slate-dir artifacts/slate \
        --out-dir artifacts/email --league mlb
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import pandas as pd


def _newest(folder: Path, pattern: str) -> pd.DataFrame | None:
    """The newest parquet matching ``pattern`` (regex on the filename), if any.

    Filenames end in a UTC ``%Y%m%dT%H%M%SZ`` stamp, so lexicographic order is
    chronological.
    """
    matches = sorted(p for p in folder.glob("*.parquet") if re.fullmatch(pattern, p.name))
    return pd.read_parquet(matches[-1]) if matches else None


def main() -> None:
    parser = argparse.ArgumentParser(description="Render the slate email body")
    parser.add_argument("--slate-dir", required=True, help="folder the runner wrote (--out)")
    parser.add_argument("--out-dir", required=True,
                        help="folder for slate_email.html + subject.txt")
    parser.add_argument("--league", default="mlb")
    args = parser.parse_args()

    from velocity.report.email_html import render_slate_email

    folder = Path(args.slate_dir)
    stamp = r"\d{8}T\d{6}Z"
    league = re.escape(args.league)
    plays = _newest(folder, rf"slate_{league}_{stamp}\.parquet")
    props = _newest(folder, rf"slate_{league}_props_{stamp}\.parquet")
    parlays = _newest(folder, rf"slate_{league}_parlays_{stamp}\.parquet")
    games_map = _newest(folder, rf"games_{league}_{stamp}\.parquet")

    generated_at = None
    for frame in (plays, props, parlays):
        if frame is not None and "generated_at" in frame.columns and len(frame):
            generated_at = frame["generated_at"].iloc[0]
            break

    subject, html = render_slate_email(
        plays, props, parlays,
        games_map=games_map, league=args.league, generated_at=generated_at,
    )

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "slate_email.html").write_text(html)
    (out / "subject.txt").write_text(subject)
    print(f"rendered email: {subject!r} → {out / 'slate_email.html'}")


if __name__ == "__main__":
    main()
