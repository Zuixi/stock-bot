#!/usr/bin/env bash
# 每日备份（P6）：业务库 + 认证库，pg_dump -Fc + sha256 + 14 天保留。
# 用法：bash scripts/backup_db.sh [备份目录]
# 生产可挂 cron；备份目录建议在宿主机持久卷（不要放容器内）。
set -euo pipefail

COMPOSE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="${1:-$COMPOSE_DIR/backups}"
STAMP="$(date +%Y%m%d_%H%M%S)"
KEEP_DAYS="${BACKUP_KEEP_DAYS:-14}"

mkdir -p "$DEST"
cd "$COMPOSE_DIR"

dump() {
  local service="$1" db="$2" user="$3" name="$4"
  local file="$DEST/${name}_${STAMP}.dump"
  echo "→ dumping $service/$db → $file"
  docker compose exec -T "$service" pg_dump -U "$user" -Fc "$db" >"$file"
  sha256sum "$file" >"$file.sha256" 2>/dev/null || shasum -a 256 "$file" >"$file.sha256"
  ls -lh "$file"
}

# 业务库在 postgres 服务；认证库在独立的 auth-db 服务（compose 里的两个 Postgres）。
dump postgres stock_bot postgres stock_bot
dump auth-db stock_bot_auth postgres stock_bot_auth

echo "→ pruning backups older than ${KEEP_DAYS} days under $DEST"
find "$DEST" -type f \( -name '*.dump' -o -name '*.dump.sha256' \) -mtime +"$KEEP_DAYS" -delete

echo "→ verifying latest checksum"
( cd "$DEST" && ls -1t stock_bot_*.dump | head -1 | xargs -I{} sh -c 'shasum -a 256 -c {}.sha256 2>/dev/null || sha256sum -c {}.sha256' )

echo "✔ backup done: $DEST"
