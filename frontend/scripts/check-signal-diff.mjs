/**
 * Phase 4 §5.5 的**纯函数**层探针：直接跑 `signalHistoryDiff.ts` 的 diff 算法。
 *
 * 为什么单独一个文件：`SignalPanel` 挂在 `/industry/:key` 上，那条路由要 `research:read`，
 * 本环境没有凭据（`E2E_RESEARCH_USER` 未设置）⇒ 组件级断言只能在 Playwright 里走
 * "mock 整个 dashboard payload" 的路子。但 **diff 算法本身**是纯函数，不需要浏览器、
 * 不需要登录——把它单独跑一遍，才能把"算法对不对"与"渲染得对不对"两件事分开证明。
 *
 * 用法：`node scripts/check-signal-diff.mjs`（无第三方依赖，`node:` 内置模块）
 * 数据：`reason` 取自库内真实行（`industry_signals` where industry_key='pig'，2026-10-13/12/09）。
 */
import { diffReason, splitReason, buildSignalHistoryRows } from "../src/features/industry-research/components/signalHistoryDiff.ts";

const R_1013 =
  "生猪均价 10.07 元/kg 低于行业平均成本 13.45 元/kg，全行业亏损；猪粮比 4.25 处于一级预警区间，行业处于亏损预警区间；能繁存栏环比连续 3 个月回落，产能去化进行中；产能去化提速，左侧布局窗口临近；猪粮比触发一级预警，国储收储预期强化底部判断";
const R_1012 =
  "生猪均价 10.15 元/kg 低于行业平均成本 13.45 元/kg，全行业亏损；猪粮比 4.34 处于一级预警区间，行业处于亏损预警区间；能繁存栏环比连续 3 个月回落，产能去化进行中；产能去化提速，左侧布局窗口临近；猪粮比触发一级预警，国储收储预期强化底部判断";
const R_1009 =
  "生猪均价 10.17 元/kg 低于行业平均成本 13.45 元/kg，全行业亏损；猪粮比 4.36 处于一级预警区间，行业处于亏损预警区间；能繁存栏环比连续 3 个月回落，产能去化进行中；产能去化提速，左侧布局窗口临近；猪粮比触发一级预警，国储收储预期强化底部判断";

let failed = 0;
function ok(cond, label, detail = "") {
  if (cond) {
    console.log(`  ✓ ${label}`);
  } else {
    failed += 1;
    console.log(`  ✘ ${label}${detail ? ` — ${detail}` : ""}`);
  }
}

console.log("== splitReason：分隔符契约（全角分号） ==");
ok(splitReason(R_1013).length === 5, "真实 reason 切成 5 段");
ok(splitReason("a；；b").length === 2, "空段被丢弃");
ok(splitReason(null).length === 0, "null → 0 段");

console.log("== diffReason：第 2 条起只给变化项 ==");
const d2 = diffReason(R_1013, R_1012);
ok(d2.length === 2, "10-12 相对 10-13 恰有 2 处变化", `实得 ${d2.length}`);
ok(
  d2.every((c) => c.kind === "value"),
  "两处都是「同一指标换值」（不是新判据）",
  JSON.stringify(d2)
);
ok(
  d2.some((c) => c.text.includes("4.34") && (c.from ?? "").includes("4.25")),
  "猪粮比 4.25 → 4.34"
);
ok(
  d2.some((c) => c.text.includes("10.15") && (c.from ?? "").includes("10.07")),
  "生猪均价 10.07 → 10.15"
);
ok(
  !d2.some((c) => c.text.includes("能繁存栏环比")),
  "未变化的「能繁存栏环比连续 3 个月回落」不出现在 diff 里"
);
ok(
  !d2.some((c) => c.text.includes("国储收储预期")),
  "未变化的「国储收储预期强化底部判断」不出现在 diff 里"
);

console.log("== 完全相同 → 0 变化（供折叠用） ==");
ok(diffReason(R_1013, R_1013).length === 0, "同一条 reason → 0 变化");

console.log("== 前缀命中分支：能繁环比 3 个月 → 2 个月 ==");
const A = "能繁存栏环比连续 3 个月回落，产能去化进行中";
const B = "能繁存栏环比连续 2 个月回落，产能去化进行中";
const dPref = diffReason(A, B);
ok(dPref.length === 1 && dPref[0].kind === "value", "识别为同一指标换值", JSON.stringify(dPref));
ok((dPref[0].from ?? "").includes("3 个月") && dPref[0].text.includes("2 个月"), "3 个月 → 2 个月");

console.log("== 已知边界：4 → 40 会被读成「值变化」（记录实测行为，不是断言它必须如此） ==");
const dTrap = diffReason("换手率 4", "换手率 40");
ok(
  dTrap.length === 1 && dTrap[0].kind === "value",
  "两段骨架相同（数字位替换后都是「换手率 #」）⇒ 判为值变化",
  JSON.stringify(dTrap)
);
ok(
  (dTrap[0].from ?? "") === "换手率 4" && dTrap[0].text === "换手率 40",
  "两端原文都完整保留 ⇒ 即使误判也不会丢信息（最坏是多一次「a → b」）"
);
ok(
  diffReason("换手率 4", "换手率 4 说明 A").length === 1 &&
    diffReason("换手率 4", "换手率 4 说明 A")[0].kind === "changed",
  "骨架不同 ⇒ 走前缀/新增分支（这才是前缀规则的用武之地）"
);

console.log("== buildSignalHistoryRows：折叠连续无变化 ==");
const rows = buildSignalHistoryRows([
  { signalType: "关注", effectiveDate: "2026-10-13", reason: R_1013 },
  { signalType: "关注", effectiveDate: "2026-10-12", reason: R_1012 },
  { signalType: "关注", effectiveDate: "2026-10-09", reason: R_1012 }, // 与上一条一模一样
  { signalType: "关注", effectiveDate: "2026-10-07", reason: R_1012 }, // 同上
]);
ok(rows.length === 3, "4 条输入 → 3 行（后 2 条无变化被折叠成 1 行）", `实得 ${rows.length}`);
ok(rows[0].kind === "signal" && rows[0].entries === 1, "首条是普通行");
ok(rows[1].changes.length === 2, "第 2 行带 2 处变化");
ok(
  rows[2].kind === "collapsed" && rows[2].entries === 2,
  "末尾是折叠行且 entries=2（条数不丢）",
  JSON.stringify(rows[2])
);
ok(
  rows.reduce((n, r) => n + r.entries, 0) === 4,
  "sum(entries) == 输入条数（没有任何一条信号被丢掉）"
);

console.log("== 单条无变化不折叠（避免为 1 条造汇总行） ==");
const rows2 = buildSignalHistoryRows([
  { signalType: "关注", effectiveDate: "2026-10-13", reason: R_1013 },
  { signalType: "关注", effectiveDate: "2026-10-12", reason: R_1013 },
]);
ok(rows2.length === 2, "2 条输入 → 2 行", `实得 ${rows2.length}`);
ok(rows2[1].unchanged === true && rows2[1].kind === "signal", "第 2 行标 unchanged 但不是折叠行");

console.log("== 空历史 / 单条 ==");
ok(buildSignalHistoryRows([]).length === 0, "空历史 → 0 行");
ok(buildSignalHistoryRows([{ signalType: "关注", effectiveDate: "d", reason: null }]).length === 1, "单条 → 1 行");

console.log(failed === 0 ? "\n全部通过" : `\n${failed} 项失败`);
process.exit(failed === 0 ? 0 : 1);
