# S12 闭环三件（记一笔 · 到期提醒 · 产物自愈）

> 交付序 **S12**。目标不是新能力，而是把已建的三块（triage / journal / regime）**接成一条回路**：
> 看到事件 → 1 步记下判断 → 到期被提醒 → 复盘 → 统计变准。

## S12.1 一键记一笔（入口直达）

- **入口**：triage 卡片底部加「记一笔」（`data-testid="triage-journal"`），链接到
  `/me?tab=journal&symbol=<code>&from=triage`（**不新增路由**，只带 query）。
- **预填**：`JournalPanel` 读 query：`symbol` 预填标的、`from=triage` 时表单上方显示一行
  「从今日体检带入：603893 瑞芯微（快照在提交时自动生成）」；`horizon_days` 默认 30（仍可改）。
- **三必填不变**：thesis / invalidation / horizon 缺一不可（预填**不得**替用户写 thesis/invalidation）。
- 提交成功后清掉 `symbol`/`from` 参数（避免刷新重复带入）。

## S12.2 到期提醒（站内，无推送）

- 新端点 `GET /api/v1/journal/summary`（登录即可，**1 条聚合查询**）→ `{open, due, reviewed}`。
- `/me` 的 Tab 标题带角标：`决策日志` 后跟 `due` 数量（0 不显示）；`今日体检` 后跟 triage 命中数（0 不显示）。
- triage 顶部一行（`data-testid="triage-due-hint"`）：`due > 0` 时显示
  「有 N 条判断今日到期 → 去复盘」（链接 `/me?tab=journal&status=due`）；`due = 0` 不渲染。
- **不做**：邮件/微信/浏览器通知/未读游标（无基础设施；"待复盘 N 条"是事实，不是未读）。

## S12.3 离线产物自愈（接 reconcile 尾步）

- 三个离线产物：`market_regime_history.json`、`peer_history_stats.json`、`triage_rule_stats.json`。
- 新增 `backend/app/services/artifacts_service.py`：
  - `async def refresh_stale_artifacts(db, day) -> dict`：逐个检查产物 `as_of`；
    **只在 `as_of < day` 时重建**（幂等）；单个产物失败 → 记录告警并**保留旧产物**，不影响其它产物与 reconcile 主流程。
  - 重建逻辑复用三个 `build_*.py` 的**纯函数入口**（脚本改成薄壳调用同一函数，禁止两处实现）。
- 接进 `reconcile_market_data_job` **尾步**（在 converge 之后），**不新开 cron**。
- 告警走既有 `job_alert_service.record_job_failure`（`/market/data-freshness` 可见）。
- **验收**：产物新鲜时零重建（断言脚本调用次数 0）；过期时调用 3 次且 as_of 前进；一个产物抛错 → 其它两个仍完成、告警 1 条、旧产物文件未被破坏（用 tmp 目录测）。

## S12.4 验收

- 行为：triage 卡片「记一笔」→ journal 表单已预填该标的（e2e，全 mock）
- 行为：`due > 0` 时 triage 顶部出现到期提示；`due = 0` 不渲染
- 行为：`/me` 两个 Tab 有角标且数字 = 后端 summary
- 行为：产物新鲜 → 0 重建；过期 → 3 重建 + `as_of` 前进；单产物失败不扩散
- 门禁：`self_review` / 全量 e2e / `doc_gate`
- 不新增表、不新增 cron、不动 `/market`

## S12.5 风险

- reconcile 尾步因此变长（三脚本合计 ~60–90s）→ 只重建过期产物；可用 `--dry-run` 观察。
- 角标数字来自新端点 → 失败时**不显示角标**（不显示 0，避免"看起来没有到期"）。
