-- Which build this is: the tier it was assembled at, and its newest stamp.
--
-- `tier` is the site's own read of what it is allowed to show. By the time it
-- gets here the private columns are already blank and the private tables
-- already empty (scripts/build_site_data.py), so this is not the enforcement —
-- it is what lets the surface SAY it is the public tier rather than silently
-- rendering a board with no prices in it.
select tier, stamp, built_at
from 'sources/velocity/data/site_meta.parquet'
