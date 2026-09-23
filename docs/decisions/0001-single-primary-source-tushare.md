# 0001 数据源收敛为单一主源 TuShare Pro

```
status:        accepted
date:          2026-09-03
supersedes:    -
superseded-by: -
```

> 追认补录（2026-09-23）。来源：`docs/references/best-practices.md`「数据源策略演进」、`plans/2026-09-03-akshare-integration.md`。

## 背景

早期数据采集采用"交易所 crawler → AKShare → yfinance"的降级链路：任一家不可用就降级到下一家。实践暴露两个问题：各家字段口径与覆盖范围不一致（同一指标在不同源上含义不同），且每接一家都要独立维护限流、反爬、字段映射与容错。

## 决策

收敛为**单一主源 TuShare Pro**，统一接口与口径。批量拉取按 `trade_date` 循环（220 交易日/年）而非按 `ts_code`（5000+ 股票），把请求次数降低一个数量级。

## 影响

- 得到：一份口径、一套限流与重试封装，接入新数据只需"client 方法 → ingest → model/迁移 → repo → service → worker → API"链路扩展，不必再改降级拓扑
- 代价：**单点依赖** TuShare（配额与可用性即全局可用性），需要 token 与配额管理
- 遗留：AKShare 仍在代码中作部分场景的兜底实现（如概念板块），但**并未被验证可用**——容器内 `py_mini_racer` 缺 `libstdc++.so.6`，兜底路径实际不可达（见 ADR 0004 与 best-practices）

## 备选方案与否决原因

- **保持多源降级链** —— 口径不一致 + 维护面随源数线性增长；降级只在"主源挂了"时有用，而主源与备源字段不可对齐时降级本身就是数据事故
- **自建交易所 crawler 为主** —— 反爬与合规成本高，且需自行维护交易日历、复权因子等基础数据
- **yfinance 为主** —— A 股覆盖与字段完整度不足
