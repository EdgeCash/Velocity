"""sportsbookreviewsonline (sbro) NCAAB odds archives — free historical closes.

The N3 backtest's market (docs/BUILD_NCAAB.md): sbro publishes free
season-long odds archives for NCAA basketball, 2007-08 through 2021-22 —
open and close for spread and total plus moneylines, one xlsx (or, for the
final season, an HTML table) per season. That is the closing number the
walk-forward grades against, the same role the CFBD lines play for NCAAF.

Wire format (both transports share it): eleven columns
``Date/Rot/VH/Team/1st/2nd/Final/Open/Close/ML/2H``, **two rows per game**
— visitor first, then home (neutral games carry ``N`` on both rows, away
side first). The ``Open``/``Close`` cells multiplex spread and total: one
row of the pair carries the spread (on the *favorite's* row), the other
the game total; the smaller of the two numbers is the spread. ``pk`` is a
zero spread; ``NL`` means no line was posted.

Team names are concatenated ("AppalachianSt", "MiamiFlorida"); matching to
the hoopR/ESPN ``location`` space uses compacted-name equality plus a
hand-checked alias table, exactly the Torvik-prior pattern.
"""

from __future__ import annotations

import re

import numpy as np
import pandas as pd

SBRO_XLSX_URL = (
    "https://www.sportsbookreviewsonline.com/wp-content/uploads/"
    "sportsbookreviewsonline_com_737/ncaa-basketball-{first}-{second:02d}.xlsx"
)
SBRO_HTML_URL = (
    "https://www.sportsbookreviewsonline.com/scoresoddsarchives/"
    "ncaa-basketball-{first}-{second:02d}/"
)
SBRO_COLUMNS = ("Date", "Rot", "VH", "Team", "1st", "2nd", "Final",
                "Open", "Close", "ML", "2H")

# A spread beyond this is not a spread — the multiplexed Open/Close cells
# separate cleanly in practice (spreads < ~60, totals > ~90), so the cap is
# a sanity net for typo rows, not a modeling assumption.
_MAX_SPREAD = 80.0


def sbro_season_url(season: int) -> str:
    """The archive URL for a season year (2022 = 2021-22; HTML for 2022)."""
    first = int(season) - 1
    template = SBRO_HTML_URL if season >= 2022 else SBRO_XLSX_URL
    return template.format(first=first, second=int(season) % 100)


def _num(value: object) -> float:
    """One multiplexed odds cell → float (``pk`` → 0, anything else → NaN)."""
    if value is None:
        return float("nan")
    text = str(value).strip().lower()
    if text in {"pk", "p"}:
        return 0.0
    # Excel sometimes serves "6.5" as "6½" in stray rows; keep digits/dot/sign.
    try:
        return float(text)
    except ValueError:
        return float("nan")


def _split_spread_total(v_val: float, h_val: float) -> tuple[float, float]:
    """(visitor cell, home cell) → (home-positive spread, total).

    The smaller number is the spread and lives on the favorite's row: when it
    is the home row, home lays the points (positive home spread — the market's
    expected home margin, the NCAAF ``spread_line`` convention).
    """
    if np.isnan(v_val) and np.isnan(h_val):
        return float("nan"), float("nan")
    if np.isnan(v_val):
        # Only one number: a total-sized value is a total, else unusable.
        return float("nan"), h_val if h_val > _MAX_SPREAD else float("nan")
    if np.isnan(h_val):
        return float("nan"), v_val if v_val > _MAX_SPREAD else float("nan")
    spread_mag, total, home_favored = (
        (h_val, v_val, True) if h_val < v_val else (v_val, h_val, False)
    )
    if spread_mag > _MAX_SPREAD or total <= _MAX_SPREAD:
        # The pair doesn't separate into (spread, total) — typo row.
        return float("nan"), total if total > _MAX_SPREAD else float("nan")
    return (spread_mag if home_favored else -spread_mag), total


def _season_date(mmdd: object, season: int) -> pd.Timestamp:
    """sbro ``Date`` (``1105``/``404``) → the calendar date inside the season."""
    text = re.sub(r"\D", "", str(mmdd))
    if len(text) < 3:
        return pd.NaT  # type: ignore[return-value]
    month, day = int(text[:-2]), int(text[-2:])
    year = int(season) - 1 if month >= 8 else int(season)
    try:
        return pd.Timestamp(year=year, month=month, day=day)
    except ValueError:
        return pd.NaT  # type: ignore[return-value]


def normalize_sbro_season(raw: pd.DataFrame, season: int) -> pd.DataFrame:
    """One season's sbro rows → one row per game with home-positive closes.

    Output: ``date`` (ET calendar date), ``away_team``/``home_team`` (sbro
    names), ``neutral_site``, final scores, ``spread_open/spread_close``
    (home-positive), ``total_open/total_close``, ``ml_away/ml_home``,
    ``season``. Rows pair visitor-then-home; a malformed pair is skipped,
    never guessed — the frame is the market record, not an estimate.
    """
    rows = raw.to_dict("records")
    out: list[dict[str, object]] = []
    i = 0
    while i + 1 < len(rows):
        away, home = rows[i], rows[i + 1]
        vh_away = str(away.get("VH", "")).strip().upper()
        vh_home = str(home.get("VH", "")).strip().upper()
        if not ((vh_away == "V" and vh_home == "H")
                or (vh_away == "N" and vh_home == "N")):
            i += 1  # resync on stray rows
            continue
        i += 2
        date = _season_date(away.get("Date"), season)
        if pd.isna(date):
            continue
        spread_open, total_open = _split_spread_total(
            _num(away.get("Open")), _num(home.get("Open"))
        )
        spread_close, total_close = _split_spread_total(
            _num(away.get("Close")), _num(home.get("Close"))
        )
        ml_away, ml_home = _num(away.get("ML")), _num(home.get("ML"))
        out.append({
            "season": int(season),
            "date": date,
            "away_team": str(away.get("Team", "")).strip(),
            "home_team": str(home.get("Team", "")).strip(),
            "neutral_site": vh_away == "N",
            "away_score": _num(away.get("Final")),
            "home_score": _num(home.get("Final")),
            "spread_open": spread_open,
            "spread_close": spread_close,
            "total_open": total_open,
            "total_close": total_close,
            "ml_away": ml_away if abs(ml_away) >= 100 else float("nan"),
            "ml_home": ml_home if abs(ml_home) >= 100 else float("nan"),
        })
    return pd.DataFrame(out)


def parse_sbro_html(text: str) -> pd.DataFrame:
    """The 2021-22 archive page's HTML table → the wire-format frame."""
    import html as html_mod

    rows = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", text, flags=re.S):
        cells = [html_mod.unescape(re.sub(r"<[^>]+>", "", c)).strip()
                 for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, flags=re.S)]
        if len(cells) == len(SBRO_COLUMNS) and cells[0] != "Date":
            rows.append(cells)
    return pd.DataFrame(rows, columns=list(SBRO_COLUMNS))


# ---------------------------------------------------------------------------
# sbro name → hoopR/ESPN ``location`` matching. Automatic compaction handles
# most of the space; the alias table is the hand-checked residue (validated
# against every 2008–2022 archive row joined to the hoopR schedules).
# ---------------------------------------------------------------------------

# sbro name → hoopR location candidates, most-likely first. Keys are matched
# after compaction, so case/punctuation variants collapse onto one entry.
# Multi-candidate entries cover ESPN renames across seasons (the resolver
# picks whichever name exists in the caller's team universe).
SBRO_TEAM_ALIASES: dict[str, tuple[str, ...]] = {
    "AlbanyNY": ("Albany", "UAlbany"),
    "American": ("American University",),
    "Ark-FortSmith": ("Arkansas-Fort Smith",),
    "ArkMonticello": ("Arkansas-Monticello",),
    "ArkPineBluff": ("Arkansas-Pine Bluff",),
    "ArkansasLR": ("Little Rock",),
    "ArkansasLittleRock": ("Little Rock",),
    "CSBakersfield": ("Cal State Bakersfield",),
    "CSFullerton": ("Cal State Fullerton",),
    "CSNorthridge": ("Cal State Northridge",),
    "CSSanBernardino": ("Cal State San Bernardino",),
    "CalIrvine": ("UC Irvine",),
    "CalPolySLO": ("Cal Poly",),
    "CalRiverside": ("UC Riverside",),
    "CalSantaBarb": ("UC Santa Barbara",),
    "CalSantaBarbara": ("UC Santa Barbara",),
    "Centenary": ("Centenary (LA)", "Centenary Louisiana"),
    "CentralConn": ("Central Connecticut",),
    "CentralFlorida": ("UCF",),
    "CentralMich": ("Central Michigan",),
    "CharlestonSou": ("Charleston Southern",),
    "CharlotteU": ("Charlotte",),
    "CollCharleston": ("Charleston", "College of Charleston"),
    "CollOfCharleston": ("Charleston", "College of Charleston"),
    "Connecticut": ("UConn",),
    "DenverU": ("Denver",),
    "Detroit": ("Detroit Mercy",),
    "DetroitU": ("Detroit Mercy",),
    "DixieState": ("Utah Tech", "Dixie State"),
    "E.Washington": ("Eastern Washington",),
    "ECentralOklahoma": ("East Central",),
    "ETennesseeSt": ("East Tennessee State",),
    "EastTennSt.": ("East Tennessee State",),
    "EastTennState": ("East Tennessee State",),
    "EasternMich": ("Eastern Michigan",),
    "EasternWash": ("Eastern Washington",),
    "FairDickinson": ("Fairleigh Dickinson",),
    "FlaAtlantic": ("Florida Atlantic",),
    "FlaGulfCoast": ("Florida Gulf Coast",),
    "FloridaIntl": ("Florida International",),
    "FullertonSt.": ("Cal State Fullerton",),
    "GeoWashington": ("George Washington",),
    "HoustonBaptist": ("Houston Christian", "Houston Baptist"),
    "HoustonU": ("Houston",),
    "IPFW": ("Purdue Fort Wayne", "Fort Wayne", "IPFW"),
    "IdahoU": ("Idaho",),
    "IllinoisChicago": ("UIC",),
    "IndianaU": ("Indiana",),
    "LiuBrooklyn": ("Long Island University", "LIU Brooklyn"),
    "LongIsland": ("Long Island University", "LIU Brooklyn"),
    "MDBaltimoreCo": ("UMBC",),
    "MDEasternShore": ("Maryland Eastern Shore", "Maryland-Eastern Shore"),
    "McNeeseState": ("McNeese",),
    "MemphisU": ("Memphis",),
    "MiamiFlorida": ("Miami",),
    "MiamiOhio": ("Miami (OH)",),
    "MiddleTennSt": ("Middle Tennessee",),
    "MinnesotaU": ("Minnesota",),
    "MissValleySt": ("Mississippi Valley State",),
    "Mississippi": ("Ole Miss",),
    "MoKansasCity": ("Kansas City",),
    "Mt.St.Mary's": ("Mount St. Mary's", "Mount St Mary"),
    "N.CarolinaA&T": ("North Carolina A&T",),
    "N.CarolinaAT": ("North Carolina A&T",),
    "NCAsheville": ("UNC Asheville",),
    "NCCentral": ("North Carolina Central",),
    "NCCharlotte": ("Charlotte",),
    "NCGreensboro": ("UNC Greensboro",),
    "NCWilmington": ("UNC Wilmington",),
    "NDakotaSt": ("North Dakota State",),
    "NJTech": ("NJIT",),
    "NebraskaOmaha": ("Omaha",),
    "NewOrleansU": ("New Orleans",),
    "NichollsState": ("Nicholls",),
    "No.Colorado": ("Northern Colorado",),
    "NoIllinois": ("Northern Illinois",),
    "NorthMichigan": ("Northern Michigan",),
    "NorthernArz": ("Northern Arizona",),
    "Penn": ("Pennsylvania",),
    "PortlandU": ("Portland",),
    "SCUpstate": ("South Carolina Upstate",),
    "SCarUpstate": ("South Carolina Upstate",),
    "SDakotaSt": ("South Dakota State",),
    "SEMissouriSt": ("Southeast Missouri State",),
    "SEMissouriState": ("Southeast Missouri State",),
    "SaintMarysCA": ("Saint Mary's",),
    "SamHoustonSt": ("Sam Houston",),
    "SamHoustonState": ("Sam Houston",),
    "SanJoseState": ("San José State",),
    "SoCarolinaSt": ("South Carolina State",),
    "SoIllinois": ("Southern Illinois",),
    "SoMississippi": ("Southern Miss",),
    "St.FrancisNY": ("St. Francis Brooklyn",),
    "St.Josephs": ("Saint Joseph's",),
    "St.Peter's": ("Saint Peter's",),
    "StMarys-CA": ("Saint Mary's",),
    "StThomas": ("St. Thomas-Minnesota", "St. Thomas - Minnesota"),
    "StephenAustin": ("Stephen F. Austin",),
    "TXPanAmerican": ("UT Rio Grande Valley",),
    "TennMartin": ("UT Martin",),
    "TennesseeChat": ("Chattanooga",),
    "TennesseeMartin": ("UT Martin",),
    "TexSanAntonio": ("UTSA",),
    "Texas-PanAmerican": ("UT Rio Grande Valley",),
    "TexasA&MCorpus": ("Texas A&M-Corpus Christi",),
    "TexasAMCorpus": ("Texas A&M-Corpus Christi",),
    "TexasArlington": ("UT Arlington",),
    "TexasSanAntonio": ("UTSA",),
    "TowsonState": ("Towson",),
    "UL-Lafayette": ("Louisiana",),
    "ULLafayette": ("Louisiana",),
    "UMKC": ("Kansas City",),
    "USCUpstate": ("South Carolina Upstate",),
    "UTRioGrandValley": ("UT Rio Grande Valley",),
    "UtahU": ("Utah",),
    "UtahValleySt": ("Utah Valley",),
    "VaCommonwealth": ("VCU",),
    "VaMilitary": ("VMI",),
    "WashingtonU": ("Washington",),
    "WesternKy": ("Western Kentucky",),
    "WinstonSalemState": ("Winston Salem", "Winston-Salem State"),
    "Wisc-GreenBay": ("Green Bay",),
    "WiscGreenBay": ("Green Bay",),
    "WiscMilwaukee": ("Milwaukee",),
    "zzzzNDakotaSt": ("North Dakota State",),
}


def _compact(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(name).lower())


_ALIASES_COMPACT: dict[str, tuple[str, ...]] = {
    _compact(k): v for k, v in SBRO_TEAM_ALIASES.items()
}


def sbro_team_lookup(teams: object) -> dict[str, str]:
    """Compacted-name lookup for a hoopR team universe.

    Keys every hoopR location by its compacted form plus common sbro
    abbreviation expansions, so ``resolve`` can map an sbro name in O(1).
    """
    lookup: dict[str, str] = {}
    for team in sorted(teams):  # type: ignore[call-overload]
        lookup.setdefault(_compact(team), str(team))
    return lookup


def join_sbro_closes(closes: pd.DataFrame, games: pd.DataFrame) -> pd.DataFrame:
    """sbro season closes → a ``game_id``-keyed frame in hoopR home orientation.

    Per season: resolve both names against that season's own team universe
    (renames stay season-correct), then match on (ET date, home, away) with
    two fallbacks — swapped order (sbro's neutral-site designation is
    arbitrary) and ±1 day (late tips cross the UTC date line). A flipped
    match negates the spread and swaps the moneylines, so every output row
    is oriented to the hoopR home team. Two safety gates: ambiguous
    ``game_id`` matches are dropped entirely, and a matched row whose final
    scores disagree with the hoopR finals is dropped — a wrong-game join is
    worse than a missing line. Output columns: ``game_id, season,
    spread_open, spread_close, total_open, total_close, ml_home, ml_away``.
    """
    out: list[dict[str, object]] = []
    for season_key, cl in closes.groupby("season"):
        season = int(str(season_key))
        g = games.loc[games["season"] == season]
        if g.empty:
            continue
        lookup = sbro_team_lookup(set(g["home_team"]) | set(g["away_team"]))
        date_et = (pd.to_datetime(g["kickoff"]) - pd.Timedelta(hours=5)).dt.normalize()
        by_key: dict[tuple[pd.Timestamp, str, str], tuple[str, float, float]] = {}
        for row, date in zip(g.to_dict("records"), date_et, strict=True):
            by_key[(pd.Timestamp(date), str(row["home_team"]), str(row["away_team"]))] = (
                str(row["game_id"]),
                float(row["home_score"]),
                float(row["away_score"]),
            )
        for r in cl.to_dict("records"):
            home = resolve_sbro_team(str(r["home_team"]), lookup)
            away = resolve_sbro_team(str(r["away_team"]), lookup)
            if home is None or away is None:
                continue
            date = pd.Timestamp(str(r["date"]))
            hit, flipped = None, False
            for delta in (0, 1, -1):
                d = date + pd.Timedelta(days=delta)
                hit, flipped = by_key.get((d, home, away)), False
                if hit is None:
                    hit, flipped = by_key.get((d, away, home)), True
                if hit is not None:
                    break
            if hit is None:
                continue
            game_id, home_final, away_final = hit
            sb_home = float(str(r["home_score"]))
            sb_away = float(str(r["away_score"]))
            if flipped:
                sb_home, sb_away = sb_away, sb_home
            if not (sb_home == home_final and sb_away == away_final):
                continue  # a wrong-game join is worse than a missing line
            sign = -1.0 if flipped else 1.0
            ml_home = r["ml_away"] if flipped else r["ml_home"]
            ml_away = r["ml_home"] if flipped else r["ml_away"]
            out.append({
                "game_id": game_id,
                "season": season,
                "spread_open": sign * float(str(r["spread_open"])),
                "spread_close": sign * float(str(r["spread_close"])),
                "total_open": float(str(r["total_open"])),
                "total_close": float(str(r["total_close"])),
                "ml_home": float(str(ml_home)),
                "ml_away": float(str(ml_away)),
            })
    frame = pd.DataFrame(out)
    if frame.empty:
        return frame
    return frame.drop_duplicates(subset="game_id", keep=False).reset_index(drop=True)


def resolve_sbro_team(name: str, lookup: dict[str, str]) -> str | None:
    """One sbro team name → the hoopR location, or None (never a guess)."""
    compact = _compact(name)
    for candidate in _ALIASES_COMPACT.get(compact, ()):  # hand-checked first
        found = lookup.get(_compact(candidate))
        if found is not None:
            return found
    found = lookup.get(compact)
    if found is not None:
        return found
    # Torvik-style trailing-abbreviation expansions, tried in order.
    for suffix, expansion in (("st", "state"), ("u", "university")):
        if compact.endswith(suffix):
            found = lookup.get(compact[: -len(suffix)] + expansion)
            if found is not None:
                return found
    return None
