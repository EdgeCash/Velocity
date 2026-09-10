-- Team identity: display code, brand colour lifted for the dark surface, and
-- the ESPN CDN mark. Built by scripts/build_site_data.py (build_teams), which
-- resolves it through velocity.report.assets.team_identity — the same resolver
-- the card renderers use. Any column may be empty: TeamMark falls back to the
-- code chip, and the neutral accent stands in for a missing colour.
select * from 'sources/velocity/data/teams.parquet'
