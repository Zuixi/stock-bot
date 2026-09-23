# Best Practices — 工程流程与文档（元经验）

> 2026-09-23 从 [`../best-practices.md`](../best-practices.md) 拆分（内容原样搬移，未改写）。
> 检索方式（探测器映射表）与**写入门槛**见 [`../best-practices.md`](../best-practices.md)。

## 已退场（规则进机器 · 2026-09-24）

以下条目已删除，改由门禁/权威文档承担（勿在 best-practices 重写长文）：

| 原主题 | 现由谁保证 |
|---|---|
| `QUEUES` 须登记 | `doc_gate` #8 · `architecture/backend/ARCHITECTURE.md` |
| compose 端口/服务名与文档一致 | `doc_gate` #5/#8 · `deployment/index.md` |
| `features` 入口与路由/API 一致 | `doc_gate` #3 · `features.md` |
| 前端静态校验 / 禁用 `npm run lint` | ADR 0006 · `deployment/local-dev.md` · `doc_gate` npm script 检查 |
| `TUSHARE_TOKEN=` 本地 pytest 口径 | `self_review.sh --full` · `testing/backend.md` |
| ruff format 与 CI 对齐 | `self_review.sh` [2/4] · CI `backend-lint` |
| pytest 须在 `backend/` 下跑 | `testing/backend.md` |
| README/端口镜像型文档漂移 | `docs/authority.md` · `doc_gate` |

- 调试数据空白问题时，优先直调后端 API 确认响应字段，再追代码。空字段可能来自三层中任意一层：后端未查 → schema 未定义 → 前端映射硬编码 undefined。
- Agent 起长驻服务（vite dev 等）必须"重启先清旧"：实测同一 worktree 的 vite 在 3000/3001 各挂一个无人认领，且 TaskStop/杀 npm 父进程会留下 vite 孤儿继续占端口导致下个会话端口漂移——查占用（`netstat -ano | grep :<port>`）→ 确认命令行身份（Get-CimInstance）→ `taskkill //F //T //PID`（`//T` 杀进程树，必加）再起新实例；规则已固化进 AGENTS.md「服务管理约定」。
- Agent 指令文件（AGENTS.md/CLAUDE.md）必须保持单一事实来源：CLAUDE.md 用 symlink 或一行转发指向 AGENTS.md 而非拷贝；同类沉淀文档不可并存近似命名（`best-practice.md` vs `best-practices.md` 曾同时被更新导致经验分裂，本文件即两文件合并产物）；AGENTS.md 中的命令必须实跑验证后再写入（本次发现 ruff/mypy 需 `uv run --extra dev`、frontend eslint 需先 `npm install`）。
- uv 的 `[project.optional-dependencies] dev`（ruff/mypy/pytest）默认不随 `uv sync` 安装：CI 与本地都必须显式 `uv sync --extra dev`（或 `uv run --extra dev`），否则 `uv run ruff/mypy` 报 "Failed to spawn"——这会让 Lint/TypeCheck 形同虚设并放行历史欠账；同理 pytest 若依赖真实运行 API，须标 `pytest.mark.e2e` 并在 CI 用 `-m "not e2e"` 避免测试 job 必挂。
- 多分支并行各自新增 Alembic 迁移、随后合并时，会产生两个 head 导致 `alembic upgrade head` 报 "Multiple head revisions"——在合并点新增一个 `down_revision=(链Ahead, 链Bhead)` 的空 merge 迁移（alembic merge <revA> <revB>）线性化两条链，否则 DB 迁移/CI Test job 必挂；此类 merge 迁移需 ruff-clean（去掉未用 import）。
- 认证微服务与安全凭据设计应采用"Argon2id 密码哈希 + Redis 滑动会话 / DB 快照持久化 + 短时 RS256 非对称断言签名 + 公钥 JWKS 规范分发"的完整分层，且会话与主业务库严格物理隔离以保障身份系统的独立性与高可用。
- 微服务拓扑演进中，API Gateway（如 Traefik）应作为唯一暴露的外部流量入口，下游业务 API 与前端容器必须收敛宿主机端口映射改为内网通信，并结合静态/动态中间件分层配置（Security Headers、Rate Limit、Compression）与 labels 声明式路由实现安全防护与任务防洪。
- 下游微服务践行零信任安全原则：用户身份与权限必须严格源自经过非对称签名（RS256）并经本地 JWKS 验签的断言载荷，任何未经签名的入站 `X-User-*` 请求头必须强制丢弃以杜绝伪造越权；同时前端跨节点/跨交易所 fallback 必须严格限定在 404 Not Found 状态码，避免将 401/403/500 等关键鉴权与系统错误静默吞没。
- 多用户业务数据归属设计应采用“模型与索引显式绑定 user_id + 路由层注入当前登录 Principal + 缓存键用户命名空间（`user:{user_id}:*`）隔离 + 前端 React Query 动态以 `user?.id` 门控并于登出时全量清理”的四层联动防护，彻底阻断横向越权与多端缓存串扰。
- 在微服务与 API Gateway 架构中，CI/CD 流水线应将微服务专属门禁（Lint/TypeCheck/Test）与统一网关烟雾测试（仅通过 Gateway:80 外部入口验证各路由分发连通性）结合，配合前端 E2E 隔离断言，形成从代码静态分析到黑盒流量路由的完整自动化质量屏障。
- 跨服务 JWT 契约（iss/aud/claims）绝不能靠口头约定：auth-service 与 backend 各自写一条交叉契约测试锁定同一 claim 结构与默认值（篡改即失败）；Traefik forwardAuth 永远以 GET 调用鉴权子请求且 trustForwardHeader=true 会透传客户端可伪造的 X-Forwarded-Method（可绕过 CSRF 方法判定），因此该开关必须为 false 让 Traefik 用真实原始方法覆写，同时用 strip-assertion（customRequestHeaders 置空=删除）在链首剥除客户端伪造的断言头。
- 认证接口安全评审修复应遵循"凭据最小暴露"原则：登录响应体绝不回传 session_id/csrf_token（只经 Set-Cookie 下发）、会话识别只认 HttpOnly Cookie 不留 Header 旁路（如 X-Session-Id）、登录/注册等匿名写接口也要强制 CSRF double-submit（先 GET /auth/csrf 再回显 Header）、内部端点用共享密钥（X-Internal-Token，非空即 hmac 常数时间强制）收口，并以"APP_ENV=production 必须 COOKIE_SECURE=true"类模型级校验 fail-fast 防生产误配。
- 基础设施安全收敛应遵循"默认不可达 + 会话寿命有界 + 日志不可信输入剔除"三原则：中间件凭据绝不使用 guest 类默认值且端口不映射宿主机（按需 docker exec 访问）；滑动续期会话必须叠加绝对过期上限（如 7 天）防止无限续命；审计日志只记录哈希后的会话标识（SHA-256），且仅当显式声明信任反代（trust_forwarded_for）时才解析 X-Forwarded-For，否则客户端可伪造该头污染审计 IP。
- 清理废弃 mock 文件前应先全局检索引用并在删除后执行一次完整构建回归，避免隐式动态依赖遗漏；类似的，实施计划 brief 末尾自带的防未用报错脚手架（hidden span + 死 import）按其收尾指令删除即可，落库前对"这段代码存在的理由"过一遍能直接清掉这类残留。
- 手写 Alembic 迁移的 revision ID 在多分支并行开发时是全局命名空间——先 `git log --all -S "<revision>"` 查重再落盘，活库 alembic_version 落在其他分支的 head 上时用临时隔离库验证迁移链而非硬闯活库。
- 计划 brief 说"创建"某文件前先确认它是否已存在：cninfo_client.py 已有 webapi 行情客户端（CnInfoClient/get_cninfo_client），追加公告检索客户端时新类名 + 新工厂与既有命名并存，沿用 brief 的同名工厂会静默 shadow 旧客户端把行情/指数采集换成公告协议；brief 里的"伪代码调用"以既有代码真实签名为准改写而非照抄（task_service 实际是 `trigger_*(db, req)` 包 `_dispatch_task(db, task_type, queue_key, payload)`，brief 草稿的 `dispatch_task(task_type=,routing_key=,payload=)` 并不存在）。
- 决策类文档落字必须"实测证据 + 删除范围 + 替代定位"三件套：计划里承诺的目标（如 SEO/静态可索引落地路径）可能与已上线配置（网关整站 `X-Robots-Tag: noindex`）直接冲突，此时先跑黑盒探针实测配置并以事实为准，再把决策连同原始 header 输出、被删除的目标清单、以及易被误删的相邻项定位（页脚署名是给用户的合规署名而非 SEO）一并写死——否则后人会按旧计划文本隐式复活已删目标，或把非 SEO 项当 SEO 一起删掉。
- 新产品模块（如行业投研工作台）落地前，先用单文件 HTML + CDN ECharts 做高保真交互原型验证信息架构与布局（结论先行、证据下钻、数据源权威性分级徽章），再迁移为 React 组件，可大幅降低前端返工成本；原型视觉应贴近真实技术栈（antd v5）而非另起炉灶。
- 审计实施计划时，把每个"已存在/无需改动/复用既有/实测 X"都当成待验证断言逐条对代码与真库核实（TuShare「默认显示=N」的字段不显式传 `fields` 就不返回、计划里不存在的 `_iso()` helper、规划器实际选的索引名、前后端路由与组件 props 形状）："已验证"旁注最容易把审查注意力从真缺陷上移走；计划里的实测数字必须能被计划自己的代码复现（本次发现计划自称的"34ms / 895 行"与它自己那条只回两天的 SQL（302 行）对不上）；契约收窄后要连**所有消费方与自检脚本**一起收窄——前端 TS union（`"local_calc" | "web"`）与按 Python 语法写的 grep（`Literal[...]`）互相 grep 不到，等于留了一条永久静默的漂移通道。
- 写实施计划时两个必查项：① **验收标准引用的门禁/工具必须先验证其存在**——曾把 Phase 2 验收写成"按 Tier 2 软门禁口径"，而 Tier 2（k6 API 基准）只存在于计划文档、`scripts/` 与 workflows 里从未实施，等于没设门禁；替代法是写成可执行的 EXPLAIN 索引断言测试。② **计划目标要与边缘配置对账**——网关 `sec-headers` 给整站（含 frontend 路由）打了 `X-Robots-Tag: noindex`，任何"静态可索引/SEO"目标在该配置下直接落空；写计划前 curl 一遍响应头比写完后返工便宜。
- 同一仓库被 Windows Git 与 WSL Git 双端检出时，行尾必须靠**入库的 `.gitattributes`（`* text=auto eol=lf`）**归一化，而不是各自设本地 `core.autocrlf`：只在 Windows 侧设全局值，WSL 侧就会把「index 存 LF / 工作区是 CRLF」判成 380 个文件整体改写（+76296/-76278，实际改动只有 2 个）——`git status` 彻底失效、真实改动被淹没，行尾噪声还会在 rebase 时制造假冲突；gitattributes 优先于 `core.autocrlf`、随仓库分发、双端行为一致（二进制由 `text=auto` 自动跳过）。遇到「改动行数 ≈ 文件总行数」的假 diff，先用 `git diff --ignore-cr-at-eol` 或 `git -c core.autocrlf=true status` 对账确认是行尾噪声再动手；把工作区批量重写为 LF 后还要补一次 `git add -A`——批量重写会让 index 缓存的 stat（size/ino）停在旧值，`git status` 会继续报几百个 phantom 修改而 `git diff` 已是空的，只有重新 stat 一遍才归零。
- 双端共享的工作区若某一侧曾以 root 检出（例如容器内跑过命令），先 `chown -R <user>` 回收属主再动 git：`.git` 不可写时 `git config`/`pull`/`rebase` 会直接失败或半途中断，而报错点（`could not lock config file .git/config: Permission denied`）与真实原因（目录属主）隔得很远。

> 历史说明：本文件系 2026-09-03 由 `docs/references/best-practice.md` 与 `docs/references/best-practices.md` 两文件合并而来（此前近似命名并存导致经验分裂），条目按主题归档；继续沿用"每次任务沉淀一句"的约定向对应分类追加。
- 数值型 tooltip 用「灰标签左 + 右对齐 tabular-nums 数值右」的两列式行布局（flex space-between + min-width），OHLC/涨跌随当日涨跌统一着色、量额中性——横排挤合（"开：x 高：x 低：x"）无对齐基准，是主流行情软件与其余 tooltip 的主要视觉分界。
- **门禁脚本必须兼容 macOS 自带 bash 3.2（CI 在 Linux 上跑不出来）**：`scripts/bench.sh` 带有两个从写下来就没在 macOS 真跑过的 bug，都在 `set -euo pipefail` 下才暴露：① `echo "...threshold=$THRESHOLD）..."` —— 变量名紧跟**全角括号**时 bash 会把 `）` 的字节并进变量名，报 `THRESHOLD�: unbound variable`；写法必须是 `${THRESHOLD}`（同理适用于 `（）`、`，`、`。` 等全角标点）；② `"${EXTRA_ARGS[@]}"` 在数组为空时，**bash 3.2** 下报 `EXTRA_ARGS[@]: unbound variable`（bash 4.4+ 才修），macOS 默认即 3.2.57，必须写成 `${EXTRA_ARGS[@]+"${EXTRA_ARGS[@]}"}`。教训：中文文本嵌变量的 shell 脚本要特判；新增/修改门禁脚本后必须在**本机**（而非只靠 CI）实际跑一次——本仓的 `bench.sh` 属于 Tier 1 硬门禁却常年无人本机跑，直到本次总门禁才暴露；另：同一脚本里共用常量的 `cache.set` 可能有多处，改一个 TTL 要扫全部同类调用点。
- 文档权威矩阵（`docs/authority.md`）与 CI 级 `doc_gate` 应同步落地：仅 pre-commit 校验时，绕过 hook 的 PR 仍会让 index 指向过期架构文（如 Tailwind 时代 `frontend-architecture.md`），Agent 按路由读到的栈与代码不一致。
- **"实现者不得改 docs" 这类禁令必须在 review 时**核对**，不能只靠 brief 声明**：Task 11 的 brief 明确写了不得动 `docs/Changelog.md` / `best-practices.md`（由控制器按阶段批量收口、避免各任务各自漂移），但实现者仍在同一个 commit 里把两处都改了。因为内容是**准确的**、也没重复，所以不返工；但流程上记一次。可操作的做法：① 在 task brief 里把禁令写成**可检的硬约束**（如“你的 commit 的 `--name-only` 不得出现 `docs/`”）；② review 时把 `git show <commit> --stat` 当必查项，而非只看业务 diff；③ 控制器收口时对 `docs/` 做一次 `git log` 审查，发现夹带就并入本阶段批量（而不是回退重做）。放宽场景：若某任务确实需要同源同 commit 的文档改动（如新增 metric_key 需同步台账），应由控制器在 brief 里**显式开例**，而不是默认默认。
- 新增或修改任何一门禁/检查脚本时，**必须当场做一次"故意破坏 → 确认它变红"的验证**并写进脚本注释：不做这一步的 checker 会在某个"看起来总绿"的路径上静默失效（同类先例：`self_review` 只扫工作区导致已提交文件漏检、文档里承诺的 `npm run lint` 实际不存在）。机器可检的检查项一旦建成，对应文档条目应从 best-practices 删除——规则进机器，不进文档。
- **`set -o pipefail` 下不要在管道末尾用 `grep -q`**：grep 一旦命中就退出，写端（`printf`/`cat`/上游命令）随即吃到 **SIGPIPE(141)**，`pipefail` 把整条管道判为失败 → 条件恒假、检查静默失效；**输入越大越必然复现**（实测 308KB diff 下 `slop_scan.sh` 的 8 个分类全部不命中，而小输入因写入先完成而"看起来正常"，所以冒烟测不出来）。改为 here-string：`if grep -qiE "$pat" <<< "$VAR"; then`。未纳入门禁：可靠检出需要 shellcheck 级别的数据流分析，未在工具链内——写 shell 门禁时人工复核这一点。
