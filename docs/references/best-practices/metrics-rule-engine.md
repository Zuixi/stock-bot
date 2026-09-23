# Best Practices — 指标建模与规则引擎

> 2026-09-23 从 [`../best-practices.md`](../best-practices.md) 拆分（内容原样搬移，未改写）。
> 检索方式（探测器映射表）与**写入门槛**见 [`../best-practices.md`](../best-practices.md)。

- 跨行业可复制的产品（投研工作台）应"一套资产服务所有行业"：指标单表（industry_key + nullable stock_id + metric_key + source + period）+ 代码级指标注册表（metric registry）+ 派生指标统一落表 + 源适配器隔离；接入新行业 = 配置 + 采集器，而非新表新页面。会随政策修订的参考锚点（如能繁正常保有量 4100→3900→3750）必须入库带生效日期，禁止硬编码。
- 纯函数规则引擎中所有"转多"判定分支（阶段复苏、左侧布局信号）都应显式要求正向证据在场（如盈亏口径任一非空），避免 None 缺失值在布尔短路中被静默当作"已确认"；并用无 DB 的纯单测把该不变量锁定为回归门。
- 同一指标表内并存多种频率时，"最新值"裁决必须先按 registry 注册频率过滤再比日期，且唯一约束必须把 freq 纳入冲突键/去重维度：否则月末归档行（period=月末）天然晚于日度行，会借未来日期压过当日数据；约束缺 freq 时同批 upsert 直接触发 PG "cannot affect row a second time"（事务硬失败），且月度行会覆写日度行 freq 导致 rollup 非幂等。日度→月度 rollup 行应作为独立 upsert 阶段先于依赖它的派生计算落库，并打 extra 标记区分来源；用"批内 (key, source, freq, period) 无重复 + 月末跨频合法共存"两条纯单测把不变量钉死。
- 财务三类数据必须按"原始事实/标准化事实/派生指标"三层分表，并以"报告版本父表（stock+end_date+report_type+comp_type+source+ann_date+update_flag）"承载多源与修订，禁止塞进 `(stock_id, trade_date)` 的日频宽表；派生指标必须带 `calc_method`（reported/calculated/derived）与 `quality_status`，缺失显示空而非 0，且 TuShare 已提供的权威值（如 or_yoy/netprofit_yoy）不应被自算值覆盖。
- 估值历史分位/通道只基于"有效正样本"（排除负 PE/PB 与缺失）计算，否则亏损期的负估值会被误判为极低估值；任何带时间属性的指标入库都要记录 ann_date/end_date/as_of，避免把修订后最新值回填历史造成前视偏差。
