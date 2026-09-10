-- The board, with the venue resolved to the name the venue uses for itself.
--
-- The raw feed carries book codes (`williamhill_us`, `mybookieag`,
-- `betonlineag`). No board in this genre shows a bettor a code, so the
-- display name is resolved once here and every page that reads
-- `velocity.board` inherits it.
select
  * exclude (tier, rationale),
  -- `tier` and `rationale` are written by the intel layer, which does not
  -- run on every slate. When a board has none of them pandas writes the
  -- column as all-NaN float64, and the page's `coalesce(tier, '')` then
  -- dies with "Could not convert string '' to DOUBLE" — a red box where
  -- the board should be, for a slate that is otherwise perfectly good.
  -- Pinning the type here means an untiered board renders as an untiered
  -- board instead of as an error.
  -- `nullif(..., 'nan')` because a *partially* populated float column casts
  -- its NaN rows to the literal string 'nan', which would then print as a
  -- tier on the board.
  nullif(cast(tier as varchar), 'nan') as tier,
  nullif(cast(rationale as varchar), 'nan') as rationale,
  case when venue is not null and venue <> 'sportsbook' then venue else book end
    as venue_key,
  case
    coalesce(nullif(venue, 'sportsbook'), book)
    when 'draftkings' then 'DraftKings'
    when 'fanduel' then 'FanDuel'
    when 'betmgm' then 'BetMGM'
    when 'betrivers' then 'BetRivers'
    when 'pointsbetus' then 'PointsBet'
    when 'williamhill_us' then 'Caesars'
    when 'betonlineag' then 'BetOnline'
    when 'lowvig' then 'LowVig'
    when 'bovada' then 'Bovada'
    when 'mybookieag' then 'MyBookie'
    when 'betus' then 'BetUS'
    when 'fanatics' then 'Fanatics'
    when 'espnbet' then 'ESPN BET'
    when 'hardrockbet' then 'Hard Rock'
    when 'ballybet' then 'Bally Bet'
    when 'pinnacle' then 'Pinnacle'
    when 'novig' then 'Novig'
    when 'prophetx' then 'ProphetX'
    when 'kalshi' then 'Kalshi'
    when 'polymarket' then 'Polymarket'
    else coalesce(nullif(venue, 'sportsbook'), book)
  end as venue_label
from 'sources/velocity/data/board.parquet'
