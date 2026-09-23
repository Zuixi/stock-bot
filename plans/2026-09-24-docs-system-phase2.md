# 文档系统二期（Harness 对齐 · Phase 1+2）

```
status:      completed
scope:       权威矩阵、前端双源收口、CI doc_gate、first-run、写入纪律、best-practices 退场、slop_scan
touches:     docs/** · scripts/slop_scan.sh · scripts/doc_gate.sh · .github/workflows/ci.yml · AGENTS.md · README.md · plans/index.md
updated:     2026-09-24
next-action: -
parent:      plans/2026-09-23-documentation-system.md
```

## 批次

| 批 | 内容 | 验收 |
|---|---|---|
| C1 | `authority.md` · index 路由 · `frontend-architecture` 转发 · `frontend/docs` 链 | 改前端任务只指向 `architecture/frontend/` |
| C2 | CI `doc_gate` blocking · 矩阵同步 | PR 上 doc_gate 红则失败 |
| C3 | doc_gate `hint:` 行 | 故意破坏可见修复路由 |
| C4 | index「变更写哪里」· AGENTS IMPORTANT | — |
| C5 | `deployment/first-run.md` · README/overview | — |
| C6 | best-practices 退场 ≥10 条 | `process-docs` 已登记 |
| C7 | `scripts/slop_scan.sh` · self_review 调用 | 改 QUEUES 命中分类 |

## 明确不做（本期）

- Phase 3（generated schema、test_architecture、doctor/dev_up）
- 常驻 LLM doc-gardening agent

## 复核与加固（2026-09-24，对本期产物的独立检查）

二期落地后逐项复核，修了 3 处“检查存在但形同虚设”的缺陷 + 1 处历史层缺口（详见 `docs/Changelog.md` 2026-09-24 条目）：

1. `.github/workflows/ci.yml`：`Compute base ref` 缺 `>> "$GITHUB_OUTPUT"` → `steps.base.outputs.REF` 为空串，三个基于 base 的步骤静默空转（`"$BASE"...HEAD` 退化为 `HEAD...HEAD`）；二期取消 `continue-on-error` 后这个隐患从“无声”变成“假绿”。已修。
2. `scripts/doc_gate.sh`：ADR 只增不改在 CI 下恒为空（工作区==HEAD）；新增 `DOC_GATE_ADR_BASE`（CI 传 PR base）+ 基准不可解析时报错，双向验证过红/绿。
3. `scripts/slop_scan.sh`：`DIFF=$(cat)` 读 tty stdin 会挂死（`self_review.sh` 是人手动跑的入口）；改为 `[ -t 0 ]` 时不读 stdin。
4. `docs/frontend-architecture.md`：260 行 → 11 行转发，旧文未归档（违反本仓库“归档而非删除”）→ 补 `docs/archive/frontend-architecture-tailwind-202604.md`。

同批确认无问题的项：`authority.md` 与 `index.md` 路由一致（doc_gate #11 已机械校验）；`process-docs.md`「已退场」表已登记 ≥10 条被门禁接管的规则；矩阵行与 ci.yml job 集合一致；死链 0。
