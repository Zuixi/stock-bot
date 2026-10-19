// 图表主题门禁：`from "echarts-for-react"` 只许出现在主题封装里（P1-4 / F8）。
//
// 为什么要有它（本轮实测的缺陷）：`ValuationTab` 与 `FinancialTab` 直接 `import ReactECharts
// from "echarts-for-react"`，绕过了 `shared/ui/EChart.tsx` 的 `withChartTheme`。ECharts 不消费
// CSS var，于是暗色卡片底（#1e222d）上仍是 ECharts 默认的深灰轴标签/图例 —— 对比度不足、
// 基本不可读。这类缺陷**编译期与运行时都不报错**：浅色下一切正常，只有人眼在暗色里才看得出，
// 所以必须机械化。
//
// 断言（唯一真相 = 本文件的白名单）：
//   `src/**/*.{ts,tsx}` 里每一处 `from "echarts-for-react"` / `from 'echarts-for-react'`
//   都必须落在 `ALLOWED` 内；白名单外的任何一处都报红。
// 白名单只有两个文件，且都**必须真的存在**（否则改名/搬迁后门禁会静默空转）：
//   • `src/shared/ui/EChart.tsx`        —— 全部业务图表的唯一入口（注入当前主题色板）；
//   • `src/shared/ui/kline/KlineChart.tsx` —— 需要双 pane + 十字光标联动的特例，自己注入主题。
//
// 破坏验证：给任意组件临时加一行 `import ReactECharts from "echarts-for-react";` → 必须 exit 1
// 并打印 `FAIL <file>:<line> 直接使用 echarts-for-react，绕过 EChart 的主题注入（见 plans P1-4）`。

import { existsSync, readFileSync, readdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join, relative } from "node:path";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const SRC = join(root, "src");

/** 白名单（相对 frontend/，与报错里的路径同形） */
const ALLOWED = ["src/shared/ui/EChart.tsx", "src/shared/ui/kline/KlineChart.tsx"];

let failed = 0;
const fail = (msg) => {
  console.log(`FAIL ${msg}`);
  failed += 1;
};

/** 只认模块说明符整串就是 `echarts-for-react`（子路径导入同样会绕过封装，故一并命中） */
const DIRECT_USE = /from\s+["']echarts-for-react(?:\/[^"']*)?["']/;

function walk(dir) {
  const out = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = join(dir, entry.name);
    if (entry.isDirectory()) out.push(...walk(full));
    else if (/\.(ts|tsx)$/.test(entry.name)) out.push(full);
  }
  return out;
}

// 0：白名单文件必须存在（防"文件被改名/搬迁 → 扫描不到 → 假绿"）
for (const allowed of ALLOWED) {
  if (existsSync(join(root, allowed))) {
    console.log(`PASS 白名单文件存在：${allowed}`);
  } else {
    fail(`白名单文件不存在：${allowed} —— 图表主题封装的落点变了，本门禁需同步更新白名单`);
  }
}

// 1：逐文件逐行扫描
const files = walk(SRC).sort();
let allowedHits = 0;
for (const file of files) {
  const rel = relative(root, file).split("\\").join("/");
  const lines = readFileSync(file, "utf8").split("\n");
  for (const [idx, line] of lines.entries()) {
    if (!DIRECT_USE.test(line)) continue;
    if (ALLOWED.includes(rel)) {
      allowedHits += 1;
      console.log(`PASS 白名单内引用：${rel}:${idx + 1}`);
      continue;
    }
    fail(`${rel}:${idx + 1} 直接使用 echarts-for-react，绕过 EChart 的主题注入（见 plans P1-4）`);
  }
}

if (failed) {
  console.log(`\n图表主题门禁未通过：${failed} 项（暗色下轴/图例不会跟随主题）`);
  process.exit(1);
}
console.log(
  `\n✔ 图表主题门禁通过：扫描 ${files.length} 个文件，echarts-for-react 仅出现在白名单` +
    `（${allowedHits} 处：${ALLOWED.join("、")}）`
);
