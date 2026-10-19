# S11 解禁日历（Restricted-Share Unlock）

> 交付序 **S11**。**零新表**：`share_floats` 表已存在（`ann_date / float_date / ts_code / float_share / float_ratio / holder_name / share_type`），
> TuShare 客户端已有 `fetch_share_float` —— **缺的是写入方与消费方**，不是数据源。
> 现状：表里 31,151 行、**仅 162 只股票**、`ann_date 2022-09 → 2026-10`，全仓**零 ingest 接线**。

---

## S11.1 补写入方（回填 + 增量）

- 脚本 `backfill_share_floats.py`（**待建**，落 `backend/` 脚本目录）：
  - 按 `ann_date` 窗口分批（每批 ≤90 天），调 `fetch_share_float(start, end)`，**幂等 upsert**（唯一键 `(ann_date, ts_code, holder_name, share_type)`）；
  - 默认窗口：**近 24 个月 + 未来 6 个月**（解禁是事前公告，未来窗口才是"日历"）；
  - `--coverage`：打印"有解禁计划的股票数 / 全市场股票数 / 最近一次成功窗口"；
  - 失败必须**响亮**（非零退出 + 打印上游错误），不得静默跳过。
- **不做**：不改调度（v1 手工/回补即可；若接调度，必须走既有 `QUEUES` 与 reconcile 尾步，另立 spec）。

## S11.2 消费点一：triage 探测器 `unlock_soon`

- 新增探测器（`triage_detectors`）：**未来 30 个自然日内**有解禁，且 `float_ratio ≥ 1%`。
  - `float_ratio ≥ 3%` → `severity=risk`；`1% ≤ ratio < 3%` → `attention`。
  - 文案：「30 天内解禁 1,200 万股（占流通 4.2%），解禁日 11-02」。
  - `facts`：解禁股数（万股）、占流通比（%）、解禁日。
  - `verify=kline`（点击看 K 线）；`source_table="share_floats"`。
- **数据缺失**：该股无 `share_floats` 行 → 探测器**不输出**（不猜"没有解禁"），并进 `unavailable`（若整表不可用）。
- 命名禁令：**不得**写"减持"（解禁 ≠ 减持），只能写"解禁/可流通"。

## S11.3 消费点二：journal `evaluation.unlock_in_window`

- 与 `earnings_in_window` 同构：`unlock_in_window: bool | null` + `unlock_note: string`。
  - `true` = 判断窗口 `(快照日, due_on]` 内有解禁且 `float_ratio ≥ 3%`；
  - `false` = 有解禁记录但不在窗口内（或无 ≥3% 的记录）；
  - `null` = 该股无任何解禁记录（写 `missing_reason["unlock"]="no_share_float_rows"`，**不阻塞复盘**，属降级键）。
- `unlock_note`：「窗口内含 1 次解禁（11-02，占流通 4.2%），结果可能被解禁冲击主导」。
- **PIT**：只用 `ann_date ≤ 观察日` 的记录（公告后才可知）。

## S11.4 验收判据

- 数据契约：`grep create_table` = 0（表已存在）；回填**重跑新增 0 行**（幂等）
- 行为：探测器边界——解禁日 = 今天+30 命中、今天+31 不命中；`ratio=1.0` 属 attention、`3.0` 属 risk
- 行为：无解禁记录的股票 → 探测器不输出（不是"没有解禁"）；`unavailable` 有该探测器时页面必须留痕
- 行为：journal `unlock_in_window` 三态 + `missing_reason` 只写降级键（**不阻塞复盘**）
- 门禁：`self_review --full` + 全量 e2e + 覆盖数打印（回填后 ≥1,000 只有解禁记录视为达标；未达标必须在报告里写清）

## S11.5 风险

- **TuShare 限速/权限**：回填前先 probe 一个窗口；失败则**只保留已有 162 只**并在 API/页面明示覆盖不足（不假装全市场）。
- **`float_ratio` 口径**：优先用源给的 `float_ratio`（%）；缺失时用 `float_share / 流通股本` 计算并标注 `ratio_source`。
- **不是"减持"**：解禁只是"可流通"，与减持计划是两件事，文案与探测器 key（`unlock_soon`）都不得混用。

### S11.5.1 实测口径修正（2026-10-19 probe，实现时逐条核过）

上面几条是按文档写的，跑真数据后有三处**必须按实测**执行，否则回填会丢行、卡片会错 10,000 倍：

1. **`start_date/end_date` 过滤的是 `float_date`（解禁日），不是 `ann_date`**。
   probe `20261001..20261019` 回来 `float_date` 只有 1 个取值、`ann_date` 横跨 2016…2026。
   ⇒ 回填按**解禁日窗口**分批（≤90 天/批，默认 30 天），这正好也是"解禁日历"需要的形状；
   "按公告日分批"在 API 上落不了地。
2. **单次最多 6000 行、`offset` 不得大于 100000**，而 `share_float` 是**持有人级**明细
   （实测单日 18,130 行，同一天同一只股票可有上千个持有人）：必须分页；30 天窗口轻易超过
   10 万行 ⇒ 分页到上限还没取完就把窗口对半拆开重取，**单日仍超限则失败退出**（不静默丢行）。
3. **`float_share` 单位是「股」（不是万股）、`float_ratio` 的分母是「总股本」（不是流通股本）**。
   核对：301387 某日合计 63,999,208 / `daily_basic.total_share` 10,730.19 万股 = **59.64%**，
   与源 `float_ratio` **59.6441 逐位吻合**；按"万股"读会大 10,000 倍，按"流通股本"算会大 2.5 倍
   （301387 当时流通股 4,268 万股 → 150%，而分母是总股本时是 59.6%）。卡片因此按 **万股 + 占总股本** 上屏。
   `float_ratio` 缺失的行**不参与判定**（`ratio_source` 需要新列 = 违反"零迁移"，v2 再说）。
4. **同一笔解禁会被多次公告**（初次 + 修订）：唯一键含 `ann_date`，读面必须按
   `(holder_name, share_type)` 取**最新公告**那一次再合计，否则同一笔加两次
   （实测 301387 会算成 119%，按最新公告是 59.6%）。
