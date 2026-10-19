#!/usr/bin/env bash
# ============================================================
# stock-bot 生产升级 SOP（在**服务器上**执行；本地用
#   ssh ss 'bash -s -- X.Y.Z <该 tag 指向的 commit>' < scripts/deploy_prod.sh
# 投喂，注意本脚本被 bash 从 stdin 读取，故**全程不用 heredoc**）。
#
# 为什么要求传 commit：只卡"version label == X.Y.Z"是**自证**——同一个版本号被重新构建
# 一份假镜像也能过检。真正的来源校验是"镜像 revision label == 我本地那个 tag 指向的 commit"。
# 本地取法：git fetch origin --tags && git rev-parse vX.Y.Z^{commit}（短 sha 也接受）
#
# 顺序：预检 → 备份并验读 → 镜像站拉取 + label 校验 + 改回 ghcr.io 名
#       → up -d --no-build → 收尾断言（镜像 tag / alembic / /health / 错误日志）
# 任一步失败即非零退出并打印回滚命令。
#
# 为什么这么写（每条都来自真踩过的坑）：
#   - 先备份：迁移单向，备份是唯一退路
#   - 走 ghcr.nju.edu.cn：服务器直连 ghcr 的 blob CDN 只有 20–35 KB/s（900MB ≈ 8h）
#   - 校验 OCI label 而非 digest：多架构 index 下 digest 对比易错（buildx 输出解析会错行）
#   - 拉镜像站前检查在跑慢 pull：dockerd 对同一 layer digest 有下载锁，慢 pull 会把镜像站那份卡死
#   - 不用 pkill -f：自匹配会杀掉自己的 ssh 会话
#   - 本脚本是被 `bash -s` **从 stdin 读**的：任何会读 stdin 的命令（`docker exec -i`）都会把脚本
#     剩下的内容当成它的输入吃掉 → bash 读到 EOF 后**静默提前退出**（无报错、退 0、活干一半）。
#     所以：只有 stdin 确实重定向自文件时才加 `-i`（如 `docker exec -i pg pg_restore -l < f`）；
#     只要 stdout 就不加 `-i`。
#   - pgrep 模式用 [x] 括号，避免匹配到自身命令行
#   - 不用 timeout（macOS 无，GNU 专有）
# ============================================================
set -euo pipefail

MIRROR="ghcr.nju.edu.cn/zuixi/stock-bot"   # 镜像站（快），最终要改回真名
UPSTREAM="ghcr.io/zuixi/stock-bot"         # compose 里 image: 用的名字
IMAGES=(backend frontend auth-service forward-auth)
CONTAINERS=(backend_api worker scheduler frontend auth_service forward_auth)
STACK="${STACK_DIR:-$HOME/stock-bot}"
COMPOSE=(-f docker-compose.yml -f docker-compose.prod.yml)
BACKUP_DIR="${BACKUP_DIR:-$HOME/backup}"
MIN_FREE_GB=3

die() { printf '\n✘ %b\n' "$*" >&2; exit 1; }
step() { printf '\n=== %s ===\n' "$*"; }

prev_version() {
  local img
  img=$(docker inspect backend_api --format '{{.Config.Image}}' 2>/dev/null || true)
  printf '%s' "${img##*:}"
}

trap 'printf "\n✘ 升级中断。回滚（应用层，秒级）：\n  cd %s && IMAGE_TAG=%s docker compose %s up -d --no-build\n  迁移：docker exec backend_api alembic downgrade -1（先确认该迁移写了 downgrade）\n" "$STACK" "$(prev_version)" "${COMPOSE[*]}" >&2; exit 1' ERR

usage() {
  printf '用法：bash -s -- X.Y.Z <expect-commit>   完整升级（commit 从本地取：git rev-parse vX.Y.Z^{commit}）\n'
  printf '      bash -s -- --check                只读巡检当前状态（不改任何东西）\n'
}

CHECK=0
case "${1:-}" in
  ""|-h|--help) usage; exit 0 ;;
  --check) CHECK=1 ;;
  *) VERSION="$1" ;;

esac

if [ "$CHECK" -eq 0 ]; then
  EXPECT_COMMIT="${2:-}"
  case "$VERSION" in
    main|latest|0.0) die "拒绝浮动 tag '$VERSION'：只部署 X.Y.Z 版本号" ;;
    *[!0-9.]*|.*|*.) die "版本号形状不对：'$VERSION'（应形如 0.0.11）" ;;
  esac
  [ "$(printf '%s' "$VERSION" | awk -F. '{print NF}')" = 3 ] || die "版本号应为 X.Y.Z：'$VERSION'"
  [ -n "$EXPECT_COMMIT" ] || die "缺 expect-commit 参数。只传版本号是自证（同版本号可被重建），必须对账 commit：\n  git fetch origin --tags && git rev-parse v$VERSION^{commit}"
  case "$EXPECT_COMMIT" in
    *[!0-9a-f]*) die "expect-commit 应为十六进制 sha（可短）：'$EXPECT_COMMIT'" ;;
  esac
  [ "${#EXPECT_COMMIT}" -ge 7 ] || die "expect-commit 太短（至少 7 位）"
fi

step "预检"
[ -d "$STACK" ] || die "找不到 $STACK"
cd "$STACK"
for f in docker-compose.yml docker-compose.prod.yml; do
  [ -f "$f" ] || die "缺 $f（它是发布产物的载体，不会被 pull 覆盖）"
done
free_gb=$(df -Pk / | awk 'NR==2{printf "%d", $4/1048576}')
echo "磁盘可用：${free_gb}G"
[ "$free_gb" -ge "$MIN_FREE_GB" ] || die "磁盘余量不足 ${MIN_FREE_GB}G"

# 有在跑的 pull 就先停下：它会持有 layer 下载锁，把镜像站那份也一起卡住
inflight=$(pgrep -af '[d]ocker.co[m]pose.*pull|[d]ocker pu[l]l' || true)
if [ -n "$inflight" ]; then
  printf '⚠ 检测到正在运行的 pull（会占住 layer 锁）：\n%s\n' "$inflight"
  die "先按 PID kill 掉它再重跑（不要用 pkill -f 'docker compose.*pull'：它会匹配到你自己的命令行）"
fi

if [ "$CHECK" -eq 1 ]; then
  step "当前状态（只读）"
  docker ps --format '{{.Names}}\t{{.Image}}\t{{.Status}}' | sort
  step "迁移位（容器内为准）"
  printf 'backend : %s (heads: %s)\n' \
    "$(docker exec backend_api alembic current 2>/dev/null | tail -1)" \
    "$(docker exec backend_api alembic heads 2>/dev/null | tail -1)"
  printf 'auth    : %s (heads: %s)\n' \
    "$(docker exec auth_service alembic current 2>/dev/null | tail -1)" \
    "$(docker exec auth_service alembic heads 2>/dev/null | tail -1)"
  step "/health"
  docker exec backend_api python -c 'import json,urllib.request as u;print(json.load(u.urlopen("http://localhost:8000/health")))'
  printf '\n（--check 未做任何改动）\n'
  exit 0
fi

step "备份两个库（迁移不可逆，这是唯一退路）"
ts=$(date +%Y%m%d-%H%M)
mkdir -p "$BACKUP_DIR"
dump_and_verify() { # $1=容器 $2=用户 $3=库名
  local f="$BACKUP_DIR/$3-$ts.dump"
  # 这一条**不能加 -i**（见文件头「stdin 被偷」）：只需 stdout，无需 -i
  docker exec "$1" pg_dump -U "$2" -d "$3" -Fc > "$f"
  # 这一条必须 -i（stdin 来自文件，故不会偷走脚本）
  docker exec -i postgres pg_restore -l < "$f" > /dev/null || die "备份 $f 不可读，放弃升级"
  printf '  ✔ %s (%s)\n' "$f" "$(du -h "$f" | cut -f1)"
}
dump_and_verify postgres stock_user stock_bot
dump_and_verify auth_db auth_user stock_bot_auth
# 每份 528MB，不清理会慢慢填满盘：每库只留最近 3 份（在**验读之后**才删）
for pat in 'stock_bot-*.dump' 'stock_bot_auth-*.dump'; do
  ls -t "$BACKUP_DIR"/$pat 2>/dev/null | tail -n +4 | xargs -r rm -f
done

step "拉取镜像 $VERSION（走镜像站，秒级；直连 ghcr 只有 20–35KB/s）"
for img in "${IMAGES[@]}"; do
  if docker pull "$MIRROR/$img:$VERSION"; then
    docker tag "$MIRROR/$img:$VERSION" "$UPSTREAM/$img:$VERSION"
  else
    printf '⚠ 镜像站没有 %s:$VERSION（pull-through 缓存可能滞后）→ 回退直连 ghcr.io，会很慢\n' "$img"
    docker pull "$UPSTREAM/$img:$VERSION"
  fi
done

step "校验镜像内容（比 OCI label 与 commit，不比 digest）"
revision=""
for img in "${IMAGES[@]}"; do
  got_v=$(docker image inspect "$UPSTREAM/$img:$VERSION" --format '{{index .Config.Labels "org.opencontainers.image.version"}}')
  got_r=$(docker image inspect "$UPSTREAM/$img:$VERSION" --format '{{index .Config.Labels "org.opencontainers.image.revision"}}')
  [ "$got_v" = "$VERSION" ] || die "$img 的 version label='$got_v'，期望 '$VERSION' —— 镜像来源可疑，停"
  case "$got_r" in
    "$EXPECT_COMMIT"*) ;;
    *) die "$img 的 revision label='$got_r'，与期望 commit '$EXPECT_COMMIT' 不匹配 —— 这个 tag 指向的不是你要发的那个提交，停" ;;
  esac
  printf '  ✔ %-13s version=%s revision=%s\n' "$img" "$got_v" "${got_r:0:7}"
  revision=$got_r   # 取最后一个（4 个镜像同一 commit），用于和 /health 对账
done

step "启动（up -d --no-build，migrate/migrate-auth 会自动跑迁移）"
IMAGE_TAG="$VERSION" docker compose "${COMPOSE[@]}" up -d --no-build

step "收尾断言"
for c in "${CONTAINERS[@]}"; do
  img=$(docker inspect "$c" --format '{{.Config.Image}}')
  case "$img" in *":$VERSION") printf '  ✔ %-13s %s\n' "$c" "${img##*/}" ;;
    *) die "$c 仍在跑 $img（期望 :$VERSION）" ;; esac
done
for pair in "backend_api stock_bot" "auth_service stock_bot_auth"; do
  set -- $pair
  cur=$(docker exec "$1" alembic current 2>/dev/null | tail -1)
  head=$(docker exec "$1" alembic heads 2>/dev/null | tail -1)
  [ "$cur" = "$head" ] || die "$2 迁移未到 head：current=$cur heads=$head"
  printf '  ✔ %-13s alembic current == heads (%s)\n' "$2" "$cur"
done
read -r hv hc <<< "$(docker exec backend_api python -c 'import json,urllib.request as u;d=json.load(u.urlopen("http://localhost:8000/health"));print(d.get("version"),d.get("commit"))')"
[ "$hv" = "v$VERSION" ] || die "/health version=$hv，期望 v$VERSION"
[ "$hc" = "$revision" ] || die "/health commit=$hc，与镜像 revision=$revision 不一致（跑的不是拉下来的那个镜像）"
case "$hc" in "$EXPECT_COMMIT"*) ;;
  *) die "/health commit=$hc 与本地 tag 指向的 '$EXPECT_COMMIT' 不符" ;; esac
printf '  ✔ /health version=%s commit=%s（= 镜像 label = 本地 tag v%s 指向的提交）\n' "$hv" "${hc:0:7}" "$VERSION"

printf '\n错误日志扫描（近 5 分钟，仅提示，需人看）：\n'
for c in "${CONTAINERS[@]}"; do
  n=$(docker logs --since 5m "$c" 2>&1 | grep -icE 'traceback|critical|error' || true)
  [ "$n" -gt 0 ] && printf '  ⚠ %s: %s 条\n' "$c" "$n"
done

printf '\n✔ %s 部署完成（备份：%s/*-%s.dump）\n' "$VERSION" "$BACKUP_DIR" "$ts"
