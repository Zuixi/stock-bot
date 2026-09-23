# 0007 性能门禁分级：Tier 1 硬门禁 + e2e/bench 与常规单测隔离

```
status:        accepted
date:          2026-09-09
supersedes:    -
superseded-by: -
```

> 追认补录（2026-09-23）。来源：`plans/2026-09-09-benchmark-tiers.md`、`backend/pyproject.toml` markers、`scripts/bench.sh`。

## 背景

性能回归此前只在上线后或用户投诉时才发现。而把性能测试混进常规单测会带来两个问题：CI 机器噪声让 wall-clock 断言随机失败；基准本身拖慢每次提交的反馈速度。

## 决策

- **分级门禁**：Tier 1（纯计算域：规则引擎周期判定、指标 rollup、多频仲裁、源优先级裁决、单位归一化 mapper）**硬门禁**；Tier 2（API/DB 域，环境抖动大）信息性
- **相对基线而非绝对阈值**：`benchmarks/baseline.json` 入库，门禁按"相对上次退化 X%"触发；契约变更时用 `--save-baseline` 刷新并随 PR 入库
- **用 marker 隔离**：`e2e`（需 docker 栈）与 `bench` 两类，常规测试默认 `-m "not e2e and not bench"`
- 确定性优先：固定尺寸合成输入、固定随机种子、禁用 GC 噪声、报 P50/P95

## 影响

- 得到：性能回归可量化、可门禁、可追溯；常规单测保持秒级反馈
- 代价：改动 Tier 1 热点必须跑 `scripts/bench.sh` 确认门禁绿（改 baseline 需在 PR 里说明契约变更理由）
- 遗留：Tier 1 目前仍是 wall-clock 比较，共享 CI runner 上的抖动靠阈值吸收；更彻底的路线（指令数/确定性测量）在 plan 中列为 Phase B 未实施

## 备选方案与否决原因

- **绝对 ms 阈值** —— 机器差异大，同一代码在 CI 与本地给出不同结论
- **基准混进常规单测** —— 拖慢提交反馈，且 flaky 会让人开始忽略整个套件
- **只做人工压测** —— 无法在 PR 阶段拦截回归，等于回到"上线才发现"
