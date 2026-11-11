#!/usr/bin/env bash
# 恢复演练（P6）：把最新备份恢复到临时库 → 跑冒烟 SQL → 输出 RTO → 清理。
# 「没演练过的备份等于没有备份。」每月至少跑一次，结果记入
# docs/deployment/operations.md 的演练记录表。
#
# 用法：bash scripts/restore_drill.sh [备份目录]
set -euo pipefail

COMPOSE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="${1:-$COMPOSE_DIR/backups}"
DRILL_DB="stock_bot_drill"
START="$(date +%s)"

cd "$COMPOSE_DIR"
PSQL=(docker compose exec -T postgres psql -U postgres)

LATEST="$(ls -1t "$SRC"/stock_bot_*.dump 2>/dev/null | grep -v auth | head -1 || true)"
if [[ -z "$LATEST" ]]; then
  echo "✘ $SRC 下没有 stock_bot_*.dump，先跑 scripts/backup_db.sh" >&2
  exit 1
fi

echo "→ drill source: $LATEST"
echo "→ recreate $DRILL_DB"
"${PSQL[@]}" -d postgres -c "DROP DATABASE IF EXISTS $DRILL_DB;" >/dev/null
"${PSQL[@]}" -d postgres -c "CREATE DATABASE $DRILL_DB OWNER stock_user;" >/dev/null

echo "→ restore"
docker compose exec -T postgres pg_restore -U postgres -d "$DRILL_DB" --no-owner --no-privileges <"$LATEST"

echo "→ smoke checks"
"${PSQL[@]}" -d "$DRILL_DB" -P pager=off -c "
SELECT 'stocks' AS check, count(*) AS rows FROM stocks
UNION ALL SELECT 'daily_quotes', count(*) FROM daily_quotes
UNION ALL SELECT 'alembic_version', count(*) FROM alembic_version;"
"${PSQL[@]}" -d "$DRILL_DB" -P pager=off -c "
SELECT max(trade_date) AS latest_quote FROM daily_quotes;"

"${PSQL[@]}" -d postgres -c "DROP DATABASE $DRILL_DB;" >/dev/null
END="$(date +%s)"
echo "✔ restore drill passed — RTO = $((END - START))s"
