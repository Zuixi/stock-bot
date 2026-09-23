# Best Practices — 架构与分层

> 2026-09-23 从 [`../best-practices.md`](../best-practices.md) 拆分（内容原样搬移，未改写）。
> 检索方式（探测器映射表）与**写入门槛**见 [`../best-practices.md`](../best-practices.md)。

- 分类/标签等用户可编辑的多对多关系应独立建表并采用"先删后插"的全量替换策略，避免增量 diff 逻辑复杂化；合成分类节点（如"其他"）应复用现有字段自动分组，减少用户手动维护成本；自定义标签系统应与现有分类体系独立设计（独立建表 + 独立前端组件 + 专用聚合页），避免与分类逻辑耦合。
- 当已有页面解决过同类问题时，优先复用其基础设施（schema、service、SQL、前端映射函数）而非重新实现，只需新增一个端点。
- 读路径（GET）不得隐式写库：信号/派生类结果应在 ingest 时评估落表、查询时只读存储行，最多保留"空库引导补算一次"的兜底；写通道入口（人工/CSV batch）必须按白名单校验 source，防止伪造采集适配器专属来源污染源优先级裁决。
- MQ 任务派发必须"先提交任务行、后发消息"：消费者可能在生产者事务提交前收到消息，查询不到行会静默跳过状态更新，任务永久 pending。
- Worker 的 `process` 不要用 blanket try/except 把异常吞成返回 dict：`BaseWorker.handle_message` 只把"抛异常"翻译成 failed 任务行，正常返回一律标 completed/100——数据源故障是常态，失败语义必须靠异常传播；只有未知 job type 这类"没开工"的错误才适合直接返回 failed dict（触库前拦截）。
- 内容型功能（知识库/图谱/原则）应落"迁移内 seed + JSONB 内容表 + 读路径装配"而非硬编码前端：内容单点维护在 seed 模块供迁移与单测共用，前端组件零行业知识，Playwright 断言锚定 `.ant-card-head-title` 一类标题容器以规避徽章同文案混淆。
- 多分层微服务认证必须把 Gateway 当作性能/路由层而不是唯一安全边界：业务服务仍需独立校验 JWT 的签名、iss、aud、exp/nbf 并执行对象/属性/功能级授权；服务间另用 workload identity（优先 mTLS）鉴别调用方，禁止仅凭内网、Docker network 或可伪造的 `X-User-*` 请求头建立信任。用户 access token 只在其目标资源服务链路内转发，跨服务使用 audience 限定、scope 下缩的 token exchange；异步 MQ 消息不携带 bearer token，改由服务身份认证并在任务记录中保存最小化 actor/audit 元数据。
- 小型 Docker Compose 系统选 API Gateway 时，应先按当前的服务发现、认证与限流需求收敛运维面：优先选择能直接读取容器元数据且无需额外控制面依赖的方案，同时让 FastAPI 保留 JWT 签名、iss/aud/exp/nbf 与对象级授权校验，避免把网关误当成唯一安全边界。
- 同一份数据出现在产品多个页面时必须**单一同源**（同一 API/同一 service）：landing 曾走旧的 `/market/indices`（读 index_dailies 盘后日线）而市场页走 `/market/global-indices`（东财实时快照），"每 60 秒自动刷新"轮询的却是盘后库表，EOD vs realtime 口径差被用户当作数据错误上报。新增展示面时先审现有链路能否复用，口径差异要在 UI 上如实标注（如"盘后为准"），"实时"文案不得配非实时数据源；同源化时把两端测试断言（E2E mock 端点/载荷形状、单测注册表条数）一起同步，注册表扩容类断言优先锁"集合"而非只锁"个数"。
- 跨端点共享的取值（如"数据截至日"）应收敛为**单一实现**（`max(trade_date)` 只写一处，其余调用方薄委托），但缓存是调用方各自选择的路径而非实现本身：不要为了"统一"给所有调用方强加缓存，也不要让薄委托顺手改掉老调用方的行为。空源降级责任在**使用层**：需要兜底语义的端点捕获实现抛出的异常并返回自洽空载荷（公开首页块绝不能因空库 500），只有确需该值的调用方才让异常穿透。把 `datetime.date` 放进 JSON 序列化的 `CacheClient` 时必须存 `isoformat()` 字符串并在读出侧 `date.fromisoformat` 兜底——直接存 `date` 依赖 `json.dumps(default=str)` 的隐式转换，读回是 str 而类型标注说 date，迟早出隐性类型错。
- 定时同步类系统的正确性不能寄托在"调度触发"层（edge-triggered，"在时刻 T 执行 X"），任务语义应是 level-triggered 的"收敛到期望状态"（expected vs actual 对账 + 幂等补齐循环）：触发只负责 timeliness，完整性由对账兜底——宿主睡眠/容器挂起在任何开发机都是常态；完整性判据用**行数量级**（≥0.8×全市场数）而非存在性（partial 行会挡住补齐形成死锁），派生表写入须与读缓存失效内聚在同一服务方法（调用方记得清缓存是不可靠假设）。方案全文见 plans/2026-09-17-data-sync-self-healing.md——实机首跑即在 10 日窗口内发现 7 天情绪历史盲区并自动补齐：派生表任务上线若无历史回放，上线前的日子是永久盲区，只有跨域对账能发现。

- **双口径（同源同形不同 source）必须严格分离"不污染"边界**：盘中与收盘同一份响应 schema 时，盘中分支**不**调任何会写入权威序列的副作用（落库/失效缓存等），且不变量用 `monkeypatch.setattr(boom)` 钉死（boom = "**如果**被调用就抛 AssertionError"）；落库仅在「fallback 到 close 路径」时走（即"反正已经走 close 路径了"才连带落盘），纯 close 路径本身的落库契约不变。缓存键必须独立（`market:limit-up:intra:{trade_date}` vs `market:limit-up:snapshot:{date}:{lookback}`），TTL 单独选（盘中 60s 对齐 30s 轮询半衰期），别为了"省一个 key"合并。schema 拓宽要在单次完整 pass 内做（intraday 路径会 None 的字段都改 Optional、source 拓宽到 union Literal），不要发半套；前端拿不到字段强转 `None` 会静默降级成"显示 --"，而不会报错。
