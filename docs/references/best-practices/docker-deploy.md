# Best Practices — Docker 与部署

> 2026-09-23 从 [`../best-practices.md`](../best-practices.md) 拆分（内容原样搬移，未改写）。
> 检索方式（探测器映射表）与**写入门槛**见 [`../best-practices.md`](../best-practices.md)。

- 容器「健康/运行」不等于「可服务」：编排里**没有 healthcheck 的网关**（Traefik/nginx 之类）会让 `docker compose up --wait` 在其容器刚 Running 时就返回，而它的 provider 还要一个节拍才把容器 labels 变成 router —— 这段窗口内**所有**路径都返回网关自己的 404（Traefik 的 body 恰 19 字节 `404 page not found`），且极易被 `curl -f ... | head -1` 吞掉退出码、看起来像偶发。正确做法是把健康检查断言在**真正需要的就绪条件**上（本例：`/api/rawdata` 里出现预期的 router 名），让 `--wait` 等到可路由；消费侧再加条件式轮询作纵深防御，并在 CI 里保留「失败即 dump 网关版本/容器状态/路由表/日志」的诊断步骤 —— 网关 dashboard 常只在容器内可达，诊断得用 `compose exec`。

- Docker build 缓存不可信：`COPY . .` 步骤即便显示 `DONE`（非 CACHED），实际可能未检测到文件变更（OrbStack on macOS 已知问题）。每次 rebuild 后必须 `docker exec` 验证容器内文件内容，不可仅依赖构建输出。
- Docker 多阶段构建应将依赖安装与源码复制分层，利用层缓存加速重建；前端 Dockerfile 应保留完整构建路径的同时支持 `target: runtime` 跳过构建阶段以适配网络受限环境。
- 前端多阶段镜像的运行时阶段必须通过 `COPY --from=<builder>` 获取构建产物，避免误从构建上下文复制 `dist/` 导致镜像构建失败。
- Docker Compose 编排应使用 `service_completed_successfully` 条件让迁移容器在应用服务启动前完成数据库 schema 初始化，避免应用启动时表不存在。
- Redis 持久化卷在容器编排中应先通过一次性 init 步骤统一修正目录属主，再启动业务容器，避免 `MISCONF` 导致写缓存失败并放大为上层 500。
- Python 镜像构建应使用 `uv.lock` 的 frozen 导出流程并配置国内默认源（如 TUNA）与更长 HTTP 超时，同时保留 BuildKit 缓存挂载，避免解析/下载抖动导致构建超时。
- Docker Compose 场景下 Nginx 反向代理上游应启用 Docker DNS 动态解析（`resolver 127.0.0.11` + 变量 `proxy_pass`），避免后端容器重建后因缓存旧 IP 导致持续 502。
- **`docker compose up -d <子集>` 会连带重建配置漂移的依赖服务（含 postgres），所以「卷名 pin」是环境假设、必须先与现网对账**：main 里 `volumes.postgres_data.name: stock_bot_wt_p7_postgres_data` 的预写前提是「数据在原 wt_p7 部署建的卷里」，而本机真实数据一直在项目前缀卷 `stock-bot_postgres_data`（4.2G）—— 一跑 `up` 就重建 postgres 并挂到同名空卷（或新建空卷），新集群 initdb 后 `data_init` 看到空 `stocks` 表→**全量重新播种 3 年行情**（烧 TuShare 额度、旧数据在 UI 上"消失"），而旧库其实完好无损。动手前先 `docker volume ls` + `docker volume inspect` 看哪个卷真有数据（`PG_VERSION`/`current_logfiles` 的 mtime、`du -sh` 量级一眼可辨），确认 pin 名与实际卷名一致再 `up`；事后确认旧库可用 `pg_controldata`（只读挂载即可）+ 起一个临时实例查 `count(*)` 对账，不用慌着 restore。
- 对"体量大但更新频率低"的静态映射数据，推荐"首次解析源文件并自动导出 SQL 种子，后续部署优先导入 SQL"的策略，导入策略显式分层为"SQL 种子优先、源文件解析兜底"，并在启动日志打印实际命中路径，便于排查"文件存在但未生效"的环境问题。

- 发布链路（CD → registry → 服务器 override）必须在打 tag 之前**端到端证明一次**："某服务的镜像根本没发"（CD 只发 3 个镜像而 `forward-auth` 缺失）与"tag 写法与 filters 不匹配"（发布约定 git tag 带 `v`、镜像 tag 不带 `v`，filters 必须与之一致，否则同一版本会出现两套镜像或一个都不触发）这两类缺陷在 CI 里都不报错，只在服务器上表现为 manifest unknown / 容器起不来，于是"发布"变成事故；镜像是给谁用的也得成对交付——发布侧镜像清单（build-push 步骤 × registry 实际 tag）与消费侧 override（每个应用服务显式 `image` + `pull_policy: always`，tag 写成 `${IMAGE_TAG:?…}` 强制必填）对不上就是一条静默失败路径；落地前用 `docker compose -f <base> -f <prod> config` 断言镜像数与服务名即可在本地抓住，并顺手核对 `-f` 会**关闭** `docker-compose.override.yml` 自动加载（本机环境假设不会泄漏到服务器）。
