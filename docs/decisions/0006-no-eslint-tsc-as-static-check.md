# 0006 前端不引入 eslint/prettier，静态校验以 tsc 为准

```
status:        accepted
date:          2026-09-23
supersedes:    -
superseded-by: -
```

> 追认补录（2026-09-23）。来源：`.pre-commit-config.yaml` 注释、`frontend/package.json` 与 CI 实际配置（决策生效日期不可考，按补录日记录）。

## 背景

前端需要静态防线，但引入 eslint + prettier 意味着一套规则、插件与版本的长期维护（与 TS、React、antd 版本的兼容矩阵），而本仓库的前端风险主要来自类型与数据契约错误，不是风格问题。

## 决策

- **不引入 eslint/prettier**，前端静态校验 = `npx tsc --noEmit`（pre-commit 与 CI 同口径）
- 唯一例外是自研检查脚本 `npm run check:design`（`frontend/scripts/check-design-tokens.mjs`，校验设计令牌使用），纳入门禁

## 影响

- 得到：零额外规则维护；类型错误在 commit 前就被拦住（后端类型 → API schema → 前端类型的链路是主防线）
- 代价：风格与常见陷阱（未使用变量、依赖数组、hooks 规则）**没有机械约束**，靠 review 与约定
- **已知缺陷**：`package.json` 仍残留 `"lint": "eslint ."` 脚本，但 eslint 未安装、无配置文件 → `npm run lint` **必然失败**。此前 `AGENTS.md` 的常用命令区的 `npm run lint` 即为此残留。修正口径为 `npx tsc --noEmit`（脚本本身待后续决定删除或移除依赖引用）

## 备选方案与否决原因

- **引入 eslint + prettier** —— 维护面大于收益；规则一旦与团队习惯冲突又会被 disable 掉，退化成噪声
- **只靠人工 review** —— 不可机械校验，与"门禁必须客观"的原则冲突
- **引入 biome 等一体工具** —— 同理，为风格问题引入新工具链
