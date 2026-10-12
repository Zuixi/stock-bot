// S1.5 聚合口径的**机械校验**（不依赖前端测试框架——本仓库没有 vitest/jest，
// 装一套会引入新的构建面，超出本任务范围）。
//
// 做法：vite 自带 esbuild，用它把纯函数模块 `heatmapAggregate.ts` 转成 ESM 临时文件
// 后直接 `import`，在 node 里跑**确定性**的样例数据（自写 LCG 伪随机，无外部依赖）。
//
// 校验什么（都是「不诚实就会挂」的不变量）：
//   1. **面积守恒**：聚合前后叶子面积合计必须完全一致（聚合块吸收被并入的面积）；
//   2. **计数诚实**：聚合块的 aggCount 之和 + 单独画出的叶子数 = 输入行数；
//   3. **阈值口径**：低于 minShare 的叶子不得单独成块（除 budget 保底外）；
//   4. **drill-down 可逆**：把聚合块的 aggKey 放进 expandedKeys 后，该组不再有聚合块；
//   5. **预算生效**：矩形总数不超过 maxTotal + 「每组 24 保底」的溢出量。
//
// 用法：`node scripts/check-heatmap-aggregate.mjs`（也可由 measure-heatmap.mjs 顺带跑）。
import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const SRC = new URL("../src/features/market/heatmapAggregate.ts", import.meta.url).pathname;
const outDir = mkdtempSync(join(tmpdir(), "heatmap-agg-"));
const outFile = join(outDir, "mod.mjs");

/** 用 vite 自带的 esbuild 转译 TS → ESM（保证与生产同一套语法/语义）。 */
function transpile() {
  const esbuild = new URL("../node_modules/esbuild/lib/main.js", import.meta.url).pathname;
  const code = readFileSync(SRC, "utf8");
  const result = execFileSync(
    process.execPath,
    [
      "-e",
      `const {transformSync}=require(${JSON.stringify(esbuild)});` +
        `const fs=require("fs");const out=process.argv[1];` +
        `const r=transformSync(fs.readFileSync(process.argv[2],"utf8"),{loader:"ts",format:"esm",target:"es2020"});` +
        `fs.writeFileSync(out,r.code);`,
      outFile,
      SRC,
    ],
    { encoding: "utf8" }
  );
  void code;
  void result;
}

function lcg(seed) {
  let s = seed >>> 0;
  return () => {
    s = (s * 1664525 + 1013904223) >>> 0;
    return s / 4294967296;
  };
}

/** 生成确定性样例：`groups` 个分组、每组 `perGroup` 行、幂律面积分布。 */
function sample(groups, perGroup, seed = 42) {
  const rnd = lcg(seed);
  const out = [];
  for (let g = 0; g < groups; g++) {
    const children = [];
    for (let i = 0; i < perGroup; i++) {
      // 幂律：少量大块 + 大量碎块（贴近真实市值/成交额分布）
      const value = Math.round(1e3 + Math.pow(rnd(), 4) * 5e8);
      children.push({
        name: `S${g}-${i}`,
        value,
        pct_chg: Math.round((rnd() * 20 - 10) * 100) / 100,
        symbol: `${g}${i}`,
      });
    }
    out.push({ aggKey: `group:G${g}`, children });
  }
  return out;
}

const checks = [];
function check(name, fn) {
  try {
    fn();
    checks.push({ name, ok: true });
  } catch (e) {
    checks.push({ name, ok: false, err: String(e && e.message ? e.message : e) });
  }
}

function assert(cond, msg) {
  if (!cond) throw new Error(msg);
}

const approx = (a, b, eps = 1e-6) => Math.abs(a - b) <= eps * Math.max(1, Math.abs(a), Math.abs(b));

transpile();
const mod = await import(outFile);
const { aggregateWithBudget, areaTotal, rectCount } = mod;

const flat = [{ aggKey: "all", children: sample(1, 5559)[0].children }];
const grouped = sample(5, 300);
const manyGroups = sample(32, 180);

// 1 + 2: 面积守恒 & 计数诚实（平铺 + 分组）
for (const [label, groups, budget] of [
  ["flat(5559)", flat, { minShare: 1e-4, maxTotal: 1500 }],
  ["grouped(5x300)", grouped, { minShare: 1e-4, maxTotal: 1500 }],
  ["grouped(32x180)", manyGroups, { minShare: 1e-4, maxTotal: 1500 }],
  ["no-budget", grouped, { minShare: 1e-4, maxTotal: 0 }],
]) {
  const out = aggregateWithBudget(groups, new Set(), budget);
  check(`面积守恒 ${label}`, () => {
    const before = groups.reduce((a, g) => a + areaTotal(g.children), 0);
    const after = out.reduce((a, g) => a + areaTotal(g.children), 0);
    assert(approx(before, after), `面积不守恒: ${before} → ${after}`);
  });
  check(`计数诚实 ${label}`, () => {
    const inRows = groups.reduce((a, g) => a + g.children.length, 0);
    let drawn = 0;
    let folded = 0;
    for (const g of out) {
      for (const leaf of g.children) {
        if (leaf.isAgg) folded += leaf.aggCount;
        else drawn += 1;
      }
    }
    assert(drawn + folded === inRows, `画出 ${drawn} + 聚合 ${folded} != 输入 ${inRows}`);
  });
  check(`预算生效 ${label}`, () => {
    if (!budget.maxTotal) return;
    const total = rectCount(out);
    // 每组最少 24 保底，所以允许 groups*24 的溢出
    assert(
      total <= budget.maxTotal + groups.length * 24,
      `矩形数 ${total} 超出预算 ${budget.maxTotal} + 保底溢出`
    );
  });
}

// 3: 低于阈值的叶子不得单独成块（无预算时逐组检查）
check("阈值口径 低于 minShare 不单独成块", () => {
  const groups = sample(3, 400, 7);
  const minShare = 0.02;
  const out = aggregateWithBudget(groups, new Set(), { minShare, maxTotal: 0 });
  const viewTotal = groups.reduce((a, g) => a + areaTotal(g.children), 0);
  for (const g of out) {
    for (const leaf of g.children) {
      if (leaf.isAgg) continue;
      assert(
        leaf.value / viewTotal >= minShare - 1e-12,
        `叶子 ${leaf.name} 占比 ${(leaf.value / viewTotal).toFixed(5)} < ${minShare} 却单独成块`
      );
    }
  }
});

// 4: drill-down 可逆
check("drill-down 展开后该组不再有聚合块", () => {
  const groups = sample(4, 300, 11);
  const first = aggregateWithBudget(groups, new Set(), { minShare: 1e-4, maxTotal: 900 });
  const aggGroup = first.find((g) => g.children.some((c) => c.isAgg));
  assert(aggGroup, "样例数据没有产生任何聚合块，无法验证 drill-down");
  const expanded = aggregateWithBudget(groups, new Set([aggGroup.aggKey]), {
    minShare: 1e-4,
    maxTotal: 900,
  });
  const target = expanded.find((g) => g.aggKey === aggGroup.aggKey);
  assert(target, `展开后找不到组 ${aggGroup.aggKey}`);
  assert(
    !target.children.some((c) => c.isAgg),
    "展开的组里仍有聚合块"
  );
  const src = groups.find((g) => g.aggKey === aggGroup.aggKey);
  assert(
    target.children.length === src.children.length,
    `展开后应恢复全量 ${src.children.length}，实为 ${target.children.length}`
  );
});

// 5: 加权均值口径（聚合块颜色 = 面积加权平均）
check("聚合块 pct_chg = 面积加权均值", () => {
  const groups = [
    {
      aggKey: "all",
      children: [
        { name: "big", value: 1000, pct_chg: 10, symbol: "A" },
        { name: "small1", value: 10, pct_chg: -10, symbol: "B" },
        { name: "small2", value: 10, pct_chg: -10, symbol: "C" },
      ],
    },
  ];
  // minShare 取大，让两个小块被聚合
  const out = aggregateWithBudget(groups, new Set(), { minShare: 0.5, maxTotal: 0 });
  const agg = out[0].children.find((c) => c.isAgg);
  assert(agg, "应产生聚合块");
  const expected = (10 * -10 + 10 * -10) / 20;
  assert(
    Math.abs(agg.pct_chg - expected) < 1e-9,
    `加权均值应为 ${expected}，实为 ${agg.pct_chg}`
  );
  assert(agg.aggCount === 2, `aggCount 应为 2，实为 ${agg.aggCount}`);
  assert(agg.value === 20, `聚合块面积应为 20，实为 ${agg.value}`);
});

const failed = checks.filter((c) => !c.ok);
for (const c of checks) console.log(`${c.ok ? "PASS" : "FAIL"}  ${c.name}${c.ok ? "" : " — " + c.err}`);
console.log(`\n聚合口径校验：${checks.length - failed.length}/${checks.length} 通过`);
rmSync(outDir, { recursive: true, force: true });
process.exit(failed.length ? 1 : 0);
