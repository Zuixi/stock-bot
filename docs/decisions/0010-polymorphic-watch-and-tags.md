# 0010 多态关注与标签

```
status:        accepted
date:          2026-10-12
supersedes:    -
superseded-by: -
```

## 背景

既有两套表，且都硬绑 `symbol`：`stock_user_tags(user_id, symbol, tag_name)`
（`backend/app/models/stock.py:16`）与 `user_watchlists` / `user_watchlist_items`
（`backend/app/models/watchlist.py`）。而新设计要关注行业 / 指数 / 概念 / 产业链节点 / 策略 / 资讯。

## 决策

- `stock_user_tags` + `user_watchlists` / `user_watchlist_items` **收敛为** `tag` + `user_target_tag`；
  `user_target_tag` 带 `stage` / `source_module` / `reason`。
- `target_type ∈ {stock, industry, index, concept, chain_node, strategy, signal, content}`。
- `自选` = `dimension='watchlist'` 的**系统 tag**，不是一张独立的表。

## 影响

- 得到：`/me 自选` · `交易执行·关注池` · `策略引擎·候选` · `资讯·我关注的` 四视图一表。
- 代价：需要数据迁移。
- 代价：前端 `useWatchlist` 的**未登录本地存储 key 结构必须同步改**，否则登录前后关注分裂。

## 备选方案与否决原因

- **给两张旧表加 `target_type` 列** —— "自选"与"标签"语义仍重叠、两套 API 仍在
- **只加新表不动旧表** —— 长期双轨，违反禁止平行实现
