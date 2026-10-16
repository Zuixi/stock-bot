status:      completed
scope:       让 scope=market 的派生指标（spec §S1.7–§S1.13）每天自动追上 T-1，并让"薄日"不再作为值发布
touches:     backend/app/services/derived_metrics_service.py, backend/app/services/market_sentiment_composite_service.py, backend/app/services/industry_registry.py, backend/app/scheduler/jobs.py, backend/app/workers/market_data_worker.py, backend/app/schemas/{task,limit_up,market_data}.py, backend/app/api/v1/market_data.py, frontend/src/{features/sentiment,pages/sentiment}, docs/**
updated:     2026-10-16
next-action: -

# 派生指标日更收敛（2026-10-16）

## 起点：用户报的现象

`/sentiment` 的"市场情绪综合指数"停在 `数据截至 10月12日`，而当时已是 10/15；同一页的
"市场宽度与动量"停在 10/09。底层 `daily_quotes` 到 10/14 —— **数据源是新的**。

## 根因（代码级，已复核）

1. 这一族指标**只有** `backend/scripts/backfill_*.py` 会写，`app/scheduler` 与
   `app/workers` 从未引用过它们（`git log -S market_sentiment_composite -- app/scheduler/` 为空）；
   名字最像的 `sentiment_daily_job` 写的是 `market_sentiment_daily`（涨停温度计），另一张表。
2. composite 的日历 = 各成分可得日的**并集** ⇒ 各腿锚点不齐时长出"薄行"。
   实测 10/12 那行：**3/11 条腿、生效权重 0.23**（行情锚定的腿只到 10/09，而基差腿到 10/12）。
3. 读面盲取 `rows[-1]` ⇒ 这个 3 条腿的值被当作"当天的综合指数"上屏，与前一日的 10 条腿值
   直接可比地展示。数据库 `created_at` 显示：最后一次人工写入是 **2026-10-13 00:46 UTC**。

## 决策

见 [ADR 0016](../docs/decisions/0016-derived-metrics-converge.md)：**对账后置收敛 + 发布闸门
0.80 + 读面只展示"各腿齐备到 T-1"的值**。产品口径由用户拍板（三选一中的"只展示齐备值"）。

## 执行

- [x] 1. registry 加 `COMPOSITE_PUBLISH_MIN_WEIGHT = 0.80` 与原因码 `below_publish_weight`；
      导入期守卫（闸门必须**严格高于**方法下限）；`SentimentCompositeOut` 加三字段
- [x] 2. 写面闸门 `apply_publish_gate`（薄日 → `value=NULL` + 原因码 + `extra.withheld_value`），
      接进 `upsert_composite_range`；`BackfillStats.days_below_publish_weight`
- [x] 3. 读面 `select_latest_publishable_index`（跳过薄行、序列在该行截断）；
      `expected_as_of` / `lag_trading_days` / `stale`（三态，本地判定不外呼）
- [x] 4. `derived_metrics_service.converge()`：目标日由数据定（`expected_latest`）、
      10 交易日窗口、四项前置闸门、逐腿隔离、PG advisory lock、落后上限 60 交易日
- [x] 5. 挂进 `reconcile_market_data_job` 尾步（`_converge_derived_metrics`），
      告警 id `market_metrics_refresh`；分域对账（16:30/16:45）**不**触发
- [x] 6. 手动轨道：`MarketDataJobType` 加 `market_metrics`，worker 分支调同一个 `converge()`
      （结果嵌一层，避免 `status` 撞键）
- [x] 7. 前端：`SentimentComposite` 三字段 + hero 新鲜度披露（滞后 / 判不出各一档）+ 页头一句话
- [x] 8. 文档：ADR 0016、字典 §4.2 发布闸门、spec S1.13 增补、Changelog、本计划
- [x] 9. 部署与真实库验收（见下节实测结果）

## 验收结果（2026-10-16 01:35–01:40 CST，真实栈实测）

部署方式：**在本 worktree 内** `docker compose -p stock-bot build api scheduler worker frontend`
（`-p stock-bot` 是必须的：运行中的栈就是本 worktree 起的，project 名 `stock-bot`），
再 `up -d --no-deps` 只重建这四个服务 —— **不碰 postgres**，故其卷
`stock_bot_wt_p7_postgres_data`（5.3G，含全部历史）不受影响；重建前已比对运行中容器的
插值环境（`AUTH_ISSUER`/`AUTH_AUDIENCE`/`RABBITMQ_URL`）与 compose 输出逐字一致，
且两个 env 都走 `${VAR:-default}`，不需要 `.env`。

| 判据 | 实测 |
|---|---|
| ① hero `as_of` = T-1 | `as_of=2026-10-15`、`lag_trading_days=0`、`stale=false`、9/11 条腿、`effective_weight=0.84` |
| ② 薄行被覆盖 + 补出缺日 | 10/12 由 **3 条腿/0.23/32.8157** 改写为 **11 条腿/1.0/32.8160**；新增 10/13、10/14、10/15（库内行数 4002 → 4005，**只 +3**） |
| ③ 再触发一轮 | breadth/activity/capital_flows = `caught_up`（**零外呼**）；volatility 按设计重扫窗口（它正是把 QVIX 从 09-30 的 NULL 追回 10/15 的那条腿）；composite 值**不变**（27.546，幂等） |
| ④ 重启后自动追平 | 本次就是**重启 scheduler 容器**触发的：启动 +2min 的对账拉到 10/15 行情，收敛器在同一 job 内把四条腿与 composite 推到 10/15（日志 `derived-metrics leg ... latest=2026-10-15` → `converge done: status=ok target=2026-10-15 alerts=[]`） |
| ⑤ 端点全链路 | `http://localhost/api/v1/market/sentiment/composite` → HTTP 200，字段如上；`/market/data-freshness` 的 `market_metrics` 四条腿 lag 全 0；部署窗口内**无新增** `job:failures` 登记 |

**意外收获**：`qvix_50etf` 上游 CSV 于本轮恢复，且波动率腿的 `always_rescan` 策略把窗口内
（09-24 ~ 10-15）此前落成 NULL 的行**全部回填**为有值行（10/12 因此从 9 条腿变 11 条腿）。
这验证了"迟到补数必须能覆盖已落 NULL 行"的设计；同时说明 QVIX 的"遗留问题"本轮已自然消解
（若再次断供，闸门仍会兜住）。

## 风险与已知遗留

- **部署源**：线上容器跑的是本 worktree 的代码；主仓当前分支**不含**情绪分析模块，
  直接在主仓 `docker compose build` 会用旧代码覆盖线上镜像。且 worktree 下**没有**
  `docker-compose.override.yml`（缺它会挂到空卷 → 触发全量重播 3 年行情、烧 TuShare 额度）。
  部署前必须：用本 worktree 作构建源 + 带上 override，并
  `docker compose config | grep -A2 postgres_data` 核对卷名。
- **T-1 不是"今天"**：管线本身是 T-1 语义（`expected_latest` 截止昨日），故页面正常也只到
  昨日。要当日新鲜度是另一件事（`plans/2026-09-17-data-sync-self-healing.md` 已记为"另立任务"）。
- **QVIX 上游滞后**（权重最大单腿 0.12，`source_not_updated` 自 09/30）：由闸门与重新归一化
  兜住，不阻塞；找第二数据源另立任务。
- **投资行为腿**（月/周频）不在本次收敛范围内。
- 历史薄行（全库 2592 个 `< 0.8` 的有值日）**不会**被回改：收敛窗口只有 10 个交易日，
  读面闸门负责不让它们成为头条。
