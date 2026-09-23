# 决策记录（ADR）

本目录记录**已经落地、且当时存在明显替代方案**的技术与产品决策，回答"为什么现在是这样"。

## 什么时候写

判据只有一条：**未来会有人问"当初为什么不直接用 X？"**

满足则写；不满足则不写。典型不该写的情况：

- 无争议的实现细节（放代码注释或 `docs/design/`）
- 一次性任务安排、排期（放 `plans/`）
- 发生变化时应该直接改掉的配置与事实（放 `docs/architecture/` 或 `docs/deployment/`）

## 纪律

1. **只增不改**：文件落地后，正文禁止编辑；只有 `status:` 与 `superseded-by:` 两行可以改。
   若结论变化 → 新写一篇 ADR，用 `supersedes:` 指向旧篇，并把旧篇标 `status: superseded`。
   这条纪律由 `scripts/doc_gate.sh` 机械校验（比对 `git diff`），是"决策记录不会腐烂"的唯一保证。
2. **单篇 ≤ 40 行**：超过 40 行说明你在写设计文档，那应该去 `docs/design/`。
3. **编号连续**：`NNNN-短横线小写标题.md`，四位数字，不复用、不跳过。
4. **归档**：被 superseded 且无引用价值时，每季度移入 `archive/`，保留编号。

## 模板

~~~markdown
# 0000 一句话标题

```
status:        accepted | superseded | rejected
date:          YYYY-MM-DD（决策生效日期）
supersedes:    -          # 或旧编号，如 0003
superseded-by: -          # 被 supersede 时填新编号
```

## 背景
当时面对什么约束/问题（只写当时已知的信息）。

## 决策
做了什么、关键参数是什么。

## 影响
得到了什么、代价是什么、留下了什么坑。

## 备选方案与否决原因
- 方案 A —— 为什么没选
- 方案 B —— 为什么没选
~~~

## 索引

状态表见 [`index.md`](./index.md)。

## 追认补录说明

0001–0007 为 2026-09-23 一次性**追认补录**：这些决策在实施时未单独记录（理由散落在 `plans/`、`docs/Changelog.md`、`docs/references/best-practices.md` 与代码注释中）。因此其 `date` 是决策生效日期的**估值**，正文如实标注了来源。此后新增的决策应**当场记录**。
