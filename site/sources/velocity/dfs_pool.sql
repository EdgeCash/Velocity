-- Every priced player on the slate — the pool the optimizer chose from, not
-- only the roster it returned. `value` is DK's own convention, projected
-- points per $1,000 of salary.
select * from 'sources/velocity/data/dfs_pool.parquet'
