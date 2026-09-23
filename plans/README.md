# plans/ — 执行计划目录

本目录存放**任务级执行计划**（怎么做这件事、分几步、进度与决策日志），是"一次改动过程"的完整记录。

## 与其它文档的分工

| 想知道 | 去哪 |
|---|---|
| 现在是什么样 | `docs/architecture/**`、`docs/features.md` |
| 当时为什么这么选 | `docs/decisions/**` |
| 怎么一步步变成这样 | `docs/evolution.md` |
| 发布/测试怎么操作 | `docs/deployment/**`、`docs/testing/**` |
| 某项任务怎么执行的 | **本目录** |
| 沉淀下来的教训 | `docs/references/best-practices.md` |

## 文件头契约（新建计划必须包含）

计划文件前 12 行内必须有如下 5 行头部（`scripts/doc_gate.sh` 校验**本计划之后新建**的文件）：

```
status:      draft | active | completed | superseded
scope:       一句话说明这件事的范围
touches:     会被改动的路径/模块（逗号分隔）
updated:     YYYY-MM-DD
next-action: 下一步动作；completed 时写 "-"
```

**新计划创建时必须同时登记到 [`index.md`](./index.md)。**

## 关于既有计划的头部

2026-09-23 之前的 20 份计划**不回填头部**——实测其中只有 4 份在正文里写明状态，给其余 16 份补 `status: unverified` 只是噪声。既有计划的状态统一在 [`index.md`](./index.md) 中维护，依据是**可核对的事实**（文中声明 / compose 与代码现状），不靠推断。

回访旧计划时（重开、废弃、结论变化）顺手补头部即可，不专门为此改文件。

## 命名约定

`YYYY-MM-DD-短横线小写主题.md`。日期为计划创建日，与文件最后修改日期无关（后者可用 `git log -1 --date=short` 查）。
