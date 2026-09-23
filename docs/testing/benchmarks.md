# 性能基准（Tier 1 硬门禁）

设计依据与完整背景见 [`../../plans/2026-09-09-benchmark-tiers.md`](../../plans/2026-09-09-benchmark-tiers.md)；决策记录见 [ADR 0007](../decisions/0007-tiered-bench-gate-and-e2e-excluded.md)。

## 分级

| Tier | 对象 | 门禁 |
|---|---|---|
| **Tier 1** | 纯计算域 CPU 热点，无 DB：规则引擎周期判定、指标 rollup（日度→月度派生）、多频指标"最新值"仲裁、源优先级裁决、单位/行情字段归一化 mapper | **硬门禁**（相对退化超阈值即失败） |
| **Tier 2** | API / DB 域（环境抖动大） | 信息性，不阻断 |

Tier 1 用例在 `backend/tests/benchmarks/`：`test_rule_engine_bench.py`、`test_rollup_bench.py`、`test_normalization_bench.py`，用 `bench` marker 与常规单测隔离。

## 用法

```bash
bash scripts/bench.sh                    # 跑基准 + 与基线对比门禁（CI/本地同款）
bash scripts/bench.sh --save-baseline    # 刷新 benchmarks/baseline.json（基线契约变更时）
bash scripts/bench.sh --quick            # 本地冒烟：少 rounds，不门禁
bash scripts/bench.sh --allow-added      # 新增基准不判失败（CI A/B 的 head 侧传此参）
```

阈值由 `BENCH_THRESHOLD` 控制（默认 **0.12**，即相对退化 >12% 失败）。产物：`benchmarks/baseline.json`（入库）、`benchmarks/last.json`（当次结果，CI 作为 artifact 上传）。

## 基线绑硬件（最重要的一条）

**不得用入库的 `benchmarks/baseline.json` 跨机器对比** —— 本机与 CI runner 的 wall-clock 可差 30–50%。

因此 CI 的 `bench-cpu` job 做**同 runner A/B**：先把 PR 的 base commit 检出、跑一遍存**临时**基线（`BENCH_BASELINE=$RUNNER_TEMP/…`，不污染工作区里被 track 的 `baseline.json`，否则 `git checkout` 会拒切），再检出 head 跑对比。

入库的 `baseline.json` 的作用是**本机开发参考** + 基线契约的版本记录。

> 细节：base 树上可能没有 bench 脚本（首次引入基准的 PR），CI 会从 head 把 `scripts/bench.sh` / `bench_compare.py` 取回；base 侧完全没有基准套件时脚本短路，让对比走"全部新增"分支而不是报错。

## 何时允许刷新基线

只有**基线契约变更**时才可以 `--save-baseline` 并随 PR 入库，且 PR 里必须说明理由：

- 新增/删除 Tier 1 基准对象
- 被测对象的输入规模或测量口径改变（会让旧数字失去可比性）
- 换了测量工具（如 Phase B 的指令数测量）

**不允许**：为了让"退化告警"消失而刷新基线。退化要么修代码，要么在 PR 里作为设计决策被说明。

## 什么时候必须跑

- 改了 Tier 1 覆盖的纯计算热点（规则引擎 / rollup / 归一化 mapper / 源优先级）
- 改了 `scripts/bench.sh` 或 `benchmarks/baseline.json` 契约
- 大型性能优化 PR：报告里要**把页面级收益与单端点收益分开写**，不要把"测了 A"说成"B 也快了 N×"（本仓库出现过照抄历史优化数字、在当前数据集上复现不出来的情况）

## 禁止的做法

- 把 wall-clock 绝对值写死成阈值（机器差异会让同一代码给出相反结论）
- 把基准混进常规单测（拖慢提交反馈，且 flaky 会让人开始忽略整个套件）
- 用共享 runner 上的单次结果判定 <12% 的差异（噪声量级）
