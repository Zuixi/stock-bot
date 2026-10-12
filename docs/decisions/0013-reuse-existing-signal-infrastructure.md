# 0013 复用既有信号基建，不自建引擎

```
status:        accepted
date:          2026-10-12
supersedes:    -
superseded-by: -
```

## 背景

10 张图里 **7 处**出现同构的"指标 → 规则 → 信号 → 触达/动作"（情绪极端值预警 · 领先指标筛选 ·
宏观政策预期差 · 历史同类政策回测 · 公司风险红线 · 策略买卖信号 · 风控止损动作）。
若各模块自建，会有 7 套阈值逻辑与 7 种回测口径。而仓库已跑通该模式：

- `backend/app/services/cycle_engine.py`——模块注释即"纯函数规则引擎，no I/O；规则可读性优先；阈值调整应改 `industry_registry` 的配置而非本文件逻辑"
- `industry_signals`（`backend/app/models/industry_research.py:102`）——注释即 "rules engine output history"，`basis` JSONB 即证据链
- `stock_features`（`backend/app/models/feature.py`）——按 `asof_date` 分区物化

## 决策

- **规则 = Python 纯函数**（与 `cycle_engine.py` 同构）；阈值进 `industry_registry`，不进函数体。
- **信号复用 `industry_signals`**——`basis` 即证据链，不建 `signal_rules` / `signals` 新表。
- **因子复用 `stock_features` 物化模式**——不建 `factor_defs` 新表。
- **不引入规则 DSL**（不做文本规则 + 解析器）。
- `industry_metrics` 的形状（`industry_key/stock_id + metric_key + source + source_tier + freq + period + value + unit + extra` + 6 列唯一键 + 2 索引）已经是统一指标表：**加 2 列复用，不建新表**。
  **不新建 `metric_series`**——该方案**已撤销**：其形状与既有 `industry_metrics` 重复，改为加 `published_at timestamptz`（PIT，见 [ADR 0011](./0011-point-in-time-data-contract.md)）与 `caliber_version text`（口径版本）两列复用。

## 影响

- 得到：口径统一、新增信号成本低、回测口径只有一种。
- 代价：引擎必须先于消费方落地（Wave 1.5）；规则编写必须遵守"阈值不进函数"的纪律。

## 备选方案与否决原因

- **各模块自建** —— 7 套阈值 + 7 种回测口径，可信度崩塌
- **等消费方都做完再抽象** —— 届时已有 7 种实现，重构成本远高于预防
- **引入文本规则 DSL + 解析器** —— **本 ADR 明确否决**：解析器是最贵的一类代码，错一次即静默算错，而当前需求数为 0
