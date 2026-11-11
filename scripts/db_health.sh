#!/usr/bin/env bash
# 数据库健康巡检（P6）：表大小 / 死元组 / 最近 vacuum / 分区状态。
# 只读；可 cron 或人工执行。生产上等价于 GET /api/v1/ops/db-health。
set -uo pipefail

COMPOSE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PSQL=(docker compose -f "$COMPOSE_DIR/docker-compose.yml" exec -T postgres psql -U postgres -d stock_bot)

echo "== 表大小 / 死元组（Top 15） =="
"${PSQL[@]}" -P pager=off -c "
SELECT relname AS table,
       n_live_tup AS live,
       n_dead_tup AS dead,
       round(100.0 * n_dead_tup / GREATEST(n_live_tup, 1), 1) AS dead_pct,
       pg_size_pretty(pg_total_relation_size(relid)) AS size,
       last_autovacuum,
       last_autoanalyze
FROM pg_stat_user_tables
ORDER BY pg_total_relation_size(relid) DESC
LIMIT 15;"

echo
echo "== daily_quotes 分区状态 =="
"${PSQL[@]}" -P pager=off -c "
SELECT relkind,
       CASE WHEN relkind = 'p' THEN
         (SELECT count(*) FROM pg_inherits WHERE inhparent = 'daily_quotes'::regclass)
       ELSE 0 END AS partitions
FROM pg_class WHERE relname = 'daily_quotes';"

echo
echo "== 未来 3 个月分区是否齐备（非分区表返回空） =="
"${PSQL[@]}" -P pager=off -c "
SELECT to_char(m, 'YYYY_MM') AS expected_partition,
       ('daily_quotes_' || to_char(m, 'YYYY_MM')) AS table_name,
       EXISTS (
         SELECT 1 FROM pg_class c
         WHERE c.relname = 'daily_quotes_' || to_char(m, 'YYYY_MM')
       ) AS present
FROM generate_series(date_trunc('month', current_date),
                     date_trunc('month', current_date) + interval '2 months',
                     interval '1 month') AS m
WHERE EXISTS (SELECT 1 FROM pg_class WHERE relname = 'daily_quotes' AND relkind = 'p');"

echo
echo "== 膨胀告警（死元组 > 20%） =="
"${PSQL[@]}" -P pager=off -t -c "
SELECT relname
FROM pg_stat_user_tables
WHERE n_dead_tup > 0.2 * GREATEST(n_live_tup, 1)
ORDER BY n_dead_tup DESC;"
