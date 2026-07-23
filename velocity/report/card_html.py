"""Render per-game matchup cards to a standalone HTML page.

Draws the card dicts from :mod:`velocity.report.cards` — team header with logos
and records, the probable starters with headshots and season lines, a
Model Projection panel, and the Moneyline / Run Line / Total recommendation bar
(PLAY / LEAN / PASS). Logos and headshots load from MLB's official static CDN by
team / player id; when an id is missing the card falls back to text, so it always
renders.

Server-side string rendering (no client JS): the emitted file is a plain,
self-contained HTML artifact. The CDN images load when it's opened in a browser
(the pipeline output); they are blocked inside the sandboxed Artifact previewer.
"""

from __future__ import annotations

import html
from pathlib import Path
from typing import Any


def team_logo_url(team_id: str | None) -> str | None:
    return None if team_id is None else f"https://www.mlbstatic.com/team-logos/{team_id}.svg"


def headshot_url(player_id: str | None) -> str | None:
    return None if player_id is None else f"https://midfield.mlbstatic.com/v1/people/{player_id}/spots/120"


def _logo(team_id: str | None) -> str:
    url = team_logo_url(team_id)
    return "" if url is None else (
        f'<img class="logo" src="{url}" alt="" loading="lazy" '
        f"onerror=\"this.style.display='none'\">"
    )


def _mug(player_id: str | None) -> str:
    url = headshot_url(player_id)
    return "" if url is None else (
        f'<img class="mug" src="{url}" alt="" loading="lazy" onerror="this.remove()">'
    )


def _sp_line(sp: dict[str, Any]) -> str:
    hand = f' <span class="hand">{html.escape(sp["hand"])}HP</span>' if sp.get("hand") else ""
    line = f'<span class="spline">{html.escape(sp["line"])}</span>' if sp.get("line") else ""
    return f'<b>{html.escape(sp["name"])}</b>{hand}{line}'


def _record(team: dict[str, Any]) -> str:
    return f'<span class="rec">{html.escape(team["record"])}</span>' if team.get("record") else ""


def _av3(v: Any) -> str | None:
    return None if v is None else f"{float(v):.3f}".lstrip("0")  # .780


def _rate(v: Any) -> str | None:
    return None if v is None else f"{float(v):.2f}"


def _pct(v: Any) -> str | None:
    return None if v is None else f"{float(v) * 100:.1f}%"


def _int(v: Any) -> str | None:
    return None if v is None else str(int(v))


def _brl(v: Any) -> str | None:
    return None if v is None else f"{float(v):.1f}%"


# (section, label, formatter, key, rank_key) — rank shown only where a rank_key is set.
_GRID_ROWS: list[tuple[str, str, Any, str, str | None]] = [
    ("bat", "OPS", _av3, "ops", "ops_rank"),
    ("bat", "R/G", _rate, "rpg", "rpg_rank"),
    ("bat", "wRC+", _int, "wrc_plus", None),
    ("bat", "AVG", _av3, "avg", None),
    ("bat", "HR", _int, "hr", None),
    ("bat", "K%", _pct, "k_pct", None),
    ("bat", "BB%", _pct, "bb_pct", None),
    ("bat", "Barrel%", _brl, "barrel_pct", None),
    ("bat", "xwOBA", _av3, "xwoba", None),
    ("splits", "vs LHP", _av3, "vs_lhp_ops", None),
    ("splits", "vs RHP", _av3, "vs_rhp_ops", None),
    ("splits", "L15 R/G", _rate, "last_n_rpg", None),
    ("pit", "ERA", _rate, "era", "era_rank"),
    ("pit", "WHIP", _rate, "whip", "whip_rank"),
    ("pit", "K/9", _rate, "k_per_9", None),
    ("pit", "xFIP", _rate, "xfip", None),
]


def _cell(grid: dict[str, Any] | None, section: str, fmt: Any, key: str,
          rank_key: str | None) -> str:
    """One side's value cell for a grid row: formatted value + optional rank badge."""
    sect = (grid or {}).get(section) or {}
    val = fmt(sect.get(key))
    if val is None:
        return '<span class="gv none">·</span>'
    rank = (grid or {}).get(section, {}).get(rank_key) if rank_key else None
    badge = f'<sup class="rk">#{int(rank)}</sup>' if rank else ""
    return f'<span class="gv">{html.escape(val)}{badge}</span>'


def _grid(c: dict[str, Any]) -> str:
    """The descriptive stat grid: away | label | home, per row, only where data exists."""
    grids = c.get("grid") or {}
    away, home = grids.get("away"), grids.get("home")
    if not away and not home:
        return ""
    rows = ""
    for section, label, fmt, key, rank_key in _GRID_ROWS:
        a = (away or {}).get(section, {})
        h = (home or {}).get(section, {})
        if a.get(key) is None and h.get(key) is None:
            continue
        rows += (
            f'<div class="grow"><div class="gc a">{_cell(away, section, fmt, key, rank_key)}</div>'
            f'<div class="gk">{html.escape(label)}</div>'
            f'<div class="gc h">{_cell(home, section, fmt, key, rank_key)}</div></div>'
        )
    if not rows:
        return ""
    return f'<div class="grid"><h4>Team profile · league rank</h4>{rows}</div>'


def _conditions(c: dict[str, Any]) -> str:
    """The weather + park-factor strip (rendered only where present)."""
    cond = c.get("conditions") or {}
    chips = ""
    w = cond.get("weather")
    if w:
        if w.get("indoors"):
            chips += '<span class="chip">Roof closed · indoors</span>'
        else:
            if w.get("temp_f") is not None:
                chips += f'<span class="chip">{int(w["temp_f"])}°F</span>'
            if w.get("wind_mph") is not None:
                d = html.escape(str(w.get("wind_dir") or ""))
                chips += f'<span class="chip">Wind {int(w["wind_mph"])} {d}</span>'
            if w.get("precip_pct") is not None:
                chips += f'<span class="chip">Rain {int(w["precip_pct"])}%</span>'
            if w.get("roof") == "retractable":
                chips += '<span class="chip muted">retractable</span>'
    park = cond.get("park")
    if park:
        chips += (
            f'<span class="chip park {html.escape(str(park["lean"]))}">'
            f'{html.escape(str(park["name"]))} · Park {int(park["runs"])} '
            f'({html.escape(str(park["lean"]))})</span>'
        )
    return f'<div class="cond">{chips}</div>' if chips else ""


def _rec_cell(rec: dict[str, Any]) -> str:
    call = str(rec["call"]).lower()
    pick = html.escape(str(rec["pick"]))
    return (
        f'<div class="rec {call}"><div class="lab">{html.escape(rec["label"])}</div>'
        f'<div class="conf">{float(rec["conf"]):.1f}</div>'
        f'<div class="pick">{pick}</div>'
        f'<div class="badge">{str(rec["call"]).title()}</div></div>'
    )


def _card(c: dict[str, Any]) -> str:
    away, home = c["away"], c["home"]
    p = c["proj"]
    recs = "".join(_rec_cell(r) for r in c["recs"])
    return f"""
  <article class="game">
    <div class="teams">
      <div class="team away">{_logo(away["logo_id"])}
        <div class="id"><span class="abbr">{html.escape(str(away["code"]))}</span>
          <span class="name">{html.escape(away["name"])} {_record(away)}</span></div>
      </div>
      <div class="at">@</div>
      <div class="team home">
        <div class="id"><span class="abbr">{html.escape(str(home["code"]))}</span>
          <span class="name">{_record(home)} {html.escape(home["name"])}</span></div>
        {_logo(home["logo_id"])}
      </div>
    </div>
    <div class="pitchers">
      <div class="sp away">{_mug(c["away_sp"]["id"])}<div>{_sp_line(c["away_sp"])}</div></div>
      <div class="role">SP · SP</div>
      <div class="sp home"><div>{_sp_line(c["home_sp"])}</div>{_mug(c["home_sp"]["id"])}</div>
    </div>
    {_conditions(c)}
    {_grid(c)}
    <div class="proj">
      <h4>Model projection</h4>
      <div class="score">
        <div><div class="n">{p["away_runs"]:.1f}</div><div class="who">{html.escape(str(away["code"]))}</div></div>
        <div class="dash">–</div>
        <div><div class="n">{p["home_runs"]:.1f}</div><div class="who">{html.escape(str(home["code"]))}</div></div>
      </div>
      <div class="pgrid">
        <div class="cell"><span class="k">Proj total</span><span class="v">{p["total"]:.1f}</span></div>
        <div class="cell"><span class="k">Fair line</span><span class="v">{html.escape(p["fair_line"])}</span></div>
        <div class="cell"><span class="k">{html.escape(str(home["code"]))} win</span><span class="v">{p["home_win"]}%</span></div>
      </div>
    </div>
    <div class="recs">{recs}</div>
  </article>"""


_STYLE = """
:root{--bg:#0b0f14;--panel:#131b24;--panel2:#0f161d;--line:#232e3a;--text:#e6edf3;
--muted:#8b98a5;--cyan:#38cfdd;--green:#2ecc71;--amber:#f5b301;--slate:#6b7684;
--mono:ui-monospace,"SF Mono",Menlo,Consolas,monospace;
--sans:system-ui,-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;}
*{box-sizing:border-box}
body{margin:0;background:radial-gradient(1200px 600px at 50% -10%,#10202b 0%,var(--bg) 55%);
color:var(--text);font-family:var(--sans);-webkit-font-smoothing:antialiased;padding:26px 14px 52px;}
.wrap{max-width:600px;margin:0 auto}
.brand{display:flex;align-items:center;gap:9px;font-weight:700}
.brand .dot{width:9px;height:9px;border-radius:50%;background:var(--cyan);box-shadow:0 0 12px var(--cyan)}
.brand h1{font-size:16px;margin:0}
.sub{color:var(--muted);font-size:12.5px;margin:5px 0 8px}
.legend{display:flex;gap:14px;margin:8px 0 18px;font-size:11px;color:var(--muted);
text-transform:uppercase;letter-spacing:.06em;flex-wrap:wrap}
.legend span{display:inline-flex;align-items:center;gap:6px}
.legend i{width:9px;height:9px;border-radius:2px;display:inline-block}
.game{background:linear-gradient(180deg,var(--panel),var(--panel2));border:1px solid var(--line);
border-radius:14px;margin-bottom:16px;overflow:hidden}
.teams{display:grid;grid-template-columns:1fr auto 1fr;align-items:center;gap:8px;padding:14px 16px 10px}
.team{display:flex;align-items:center;gap:10px;min-width:0}
.team.home{justify-content:flex-end;text-align:right}
.team .id{display:flex;flex-direction:column;gap:1px;min-width:0}
.logo{width:38px;height:38px;flex:0 0 auto;object-fit:contain}
.abbr{font-family:var(--mono);font-size:21px;font-weight:700;letter-spacing:.03em}
.name{font-size:11px;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.name .rec{font-family:var(--mono);color:var(--text);opacity:.8}
.at{color:var(--muted);font-family:var(--mono)}
.pitchers{display:grid;grid-template-columns:1fr auto 1fr;gap:8px;padding:0 16px 12px;
font-size:12px;color:var(--muted);align-items:center}
.sp{display:flex;align-items:center;gap:8px}
.sp.home{justify-content:flex-end;text-align:right}
.mug{width:30px;height:30px;border-radius:50%;object-fit:cover;background:#22303c;flex:0 0 auto}
.pitchers b{color:var(--text);font-weight:600}
.hand{font-family:var(--mono);font-size:10px;color:var(--cyan)}
.spline{display:block;font-family:var(--mono);font-size:10.5px;opacity:.85}
.role{font-family:var(--mono);font-size:10px;letter-spacing:.05em;color:var(--cyan);text-transform:uppercase}
.proj{padding:12px 16px 4px}
.proj h4{margin:0 0 9px;font-size:10.5px;letter-spacing:.1em;text-transform:uppercase;color:var(--cyan);font-weight:700}
.score{display:flex;align-items:baseline;justify-content:center;gap:12px;margin-bottom:11px}
.score .n{font-family:var(--mono);font-size:29px;font-weight:700;font-variant-numeric:tabular-nums}
.score .dash{color:var(--muted);font-size:19px}
.score .who{font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:.05em}
.pgrid{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;padding-bottom:12px}
.pgrid .cell{display:flex;flex-direction:column;gap:3px}
.pgrid .k{font-size:9.5px;color:var(--muted);text-transform:uppercase;letter-spacing:.05em}
.pgrid .v{font-family:var(--mono);font-size:15px;font-weight:600;font-variant-numeric:tabular-nums}
.recs{display:grid;grid-template-columns:1fr 1fr 1fr;gap:1px;background:var(--line);border-top:1px solid var(--line)}
.rec{background:var(--panel2);padding:11px 6px 12px;text-align:center}
.rec .lab{font-size:9.5px;color:var(--muted);letter-spacing:.07em;text-transform:uppercase}
.rec .conf{font-family:var(--mono);font-size:26px;font-weight:700;line-height:1.15;font-variant-numeric:tabular-nums}
.rec .pick{font-size:11px;color:var(--text);font-family:var(--mono);margin-top:1px}
.rec .badge{display:inline-block;margin-top:6px;font-size:10px;font-weight:700;letter-spacing:.09em;
padding:2px 9px;border-radius:999px;text-transform:uppercase}
.play .conf{color:var(--green)}.play .badge{background:rgba(46,204,113,.14);color:var(--green)}
.lean .conf{color:var(--amber)}.lean .badge{background:rgba(245,179,1,.14);color:var(--amber)}
.pass .conf{color:var(--slate)}.pass .badge{background:rgba(107,118,132,.16);color:var(--slate)}
.cond{display:flex;flex-wrap:wrap;gap:6px;padding:2px 16px 12px}
.chip{font-family:var(--mono);font-size:10.5px;color:var(--muted);background:var(--panel2);
border:1px solid var(--line);border-radius:999px;padding:3px 9px;letter-spacing:.02em}
.chip.muted{opacity:.7}
.chip.park.hitter{color:var(--amber);border-color:rgba(245,179,1,.3)}
.chip.park.pitcher{color:var(--cyan);border-color:rgba(56,207,221,.3)}
.grid{padding:4px 16px 10px}
.grid h4{margin:0 0 7px;font-size:10.5px;letter-spacing:.1em;text-transform:uppercase;
color:var(--cyan);font-weight:700}
.grow{display:grid;grid-template-columns:1fr auto 1fr;align-items:center;gap:8px;
padding:3px 0;border-bottom:1px solid rgba(35,46,58,.5)}
.grow:last-child{border-bottom:0}
.gc{display:flex}.gc.a{justify-content:flex-start}.gc.h{justify-content:flex-end}
.gk{font-size:9.5px;color:var(--muted);text-transform:uppercase;letter-spacing:.05em;
text-align:center;min-width:64px}
.gv{font-family:var(--mono);font-size:13px;font-weight:600;font-variant-numeric:tabular-nums}
.gv.none{color:var(--slate);font-weight:400}
.rk{font-size:8.5px;color:var(--cyan);font-weight:700;margin-left:2px;top:-.4em}
footer.note{color:var(--muted);font-size:11.5px;line-height:1.6;margin-top:8px;padding:13px 14px;
border:1px dashed var(--line);border-radius:10px;background:var(--panel2)}
footer.note b{color:var(--text)}
"""


def render_cards_page(cards: list[dict[str, Any]], league: str, generated_at: str) -> str:
    """Return the full HTML page for a slate of card dicts."""
    body = "".join(_card(c) for c in cards) or '<p class="sub">No games on the board.</p>'
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Velocity — {html.escape(league.upper())} Cards</title><style>{_STYLE}</style></head>
<body><div class="wrap">
<div class="brand"><span class="dot"></span><h1>Velocity — {html.escape(league.upper())} Cards</h1></div>
<div class="sub">{len(cards)} game(s) · generated {html.escape(generated_at)}</div>
<div class="legend"><span><i style="background:var(--green)"></i>Play · conf ≥ 8</span>
<span><i style="background:var(--amber)"></i>Lean · 4–8</span>
<span><i style="background:var(--slate)"></i>Pass · &lt; 4</span></div>
{body}
<footer class="note"><b>Sources:</b> projections and recommendations are live model
output; the team profile is StatsAPI (season lines, splits) with league ranks computed
across all 30 clubs, advanced metrics from FanGraphs / Statcast, weather from Open-Meteo
(a first-pitch forecast), and committed park factors. The confidence scale is a
presentation of edge, not a calibrated number.</footer>
</div></body></html>"""


def write_cards_html(
    dest: str | Path, cards: list[dict[str, Any]], *, league: str, generated_at: str
) -> Path:
    """Render and write the cards page to ``dest``; return the path."""
    dest = Path(dest)
    dest.write_text(render_cards_page(cards, league, generated_at), encoding="utf-8")
    return dest
