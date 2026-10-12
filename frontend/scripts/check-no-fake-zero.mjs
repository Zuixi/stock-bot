// 「不得假 0」门禁（Ruling 44）。
//
// 为什么需要一条机械门禁：本仓库反复出现同一种错——**"没有数据"被渲染成 `0`**。
// `0` 在一个数值型页面上是一个**合法值**（平盘/点位 0），所以它比空白更危险：
// 用户与下游都读不出"其实是没取到"。同一类已经踩过三次：
//   ① 全球卡片改造前的 `price`/`change` 曾用 `?? 0`（本任务段 A 修正为 `null` + `price_source`）；
//   ② 详情页 `/index/<tsCode>` 对 globalCards 兜底对象写 `card.price ?? 0`（Ruling 44 修正）；
//   ③ 新页 `/market/global` 的 `fmtPrice` 若写 `v ?? 0` 会同样撒谎。
//
// 本门禁做两件事（都是**源码级**断言，不需要跑浏览器）：
//   A. **禁 `?? 0` 兜底**：在指定的行情展示文件里，禁止对价/涨跌字段使用 `?? 0`
//      （`?? null` / `?? "—"` 允许）。规则按"含 0 的 nullish 兜底"匹配，
//      不试图做类型推断——类型推断需要 ts 编译器，而它**抓不到这个语义**（`0` 与 `null`
//      在 `number | null` 下都是合法赋值，`tsc` 只会沉默）。
//   B. **必须有空值分支**：上述文件里出现 `?? null` 的那几个字段，必须同时存在
//      一个显式的空值渲染（`== null` / `=== null` 判断或 `—`/`--` 字面量），
//      防止"改成 null 了但视图直接 `null.toLocaleString()`"这种半截修法。
//
// 破坏验证（见 report）：把任一处 `?? null` 改回 `?? 0` → 本门禁必须转红。

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");

/**
 * 受检文件 + 该文件里"承载行情数值"的字段名。
 * 字段名用于 B 项的定位；A 项对整个文件生效（这些文件里的 `?? 0` 没有正当用途）。
 */
const WATCHED = [
  {
    file: "src/pages/market-index/index.tsx",
    fields: ["value", "change", "changePercent"],
    why: "指数/全球标的详情页（globalCards 兜底对象，Ruling 44）",
  },
  {
    file: "src/pages/market-global/index.tsx",
    fields: ["price"],
    why: "全球市场子页（S1.3 段 A，卡片数值一律可空）",
  },
  {
    file: "src/features/market/components/GlobalIndexCardView.tsx",
    fields: ["price", "change", "pctChange"],
    why: "全球指数卡片（市场首页）",
  },
];

let failed = 0;
const fail = (msg) => {
  console.log(`FAIL ${msg}`);
  failed += 1;
};
const pass = (msg) => console.log(`PASS ${msg}`);

for (const { file, fields, why } of WATCHED) {
  const text = readFileSync(join(root, file), "utf8");
  const lines = text.split("\n");

  // A. 禁"把缺值兜成 0"的**取值型**写法。
  //
  // 只在这个 `?? 0` 会**产出被使用的值**时才算违规；`(x ?? 0) > 0` 这类
  // "折成 0 后再比大小"的写法只影响分支判定、不产出展示值，故只告警不计失败
  // （本仓库 GlobalIndexCardView 曾用它选涨跌色；已改为严格比较，但门禁不应
  //  把"能改好但没撒谎"的写法判成撒谎——假阳性会让下一个人直接放宽规则）。
  const fakeZero = [];
  const softZero = [];
  lines.forEach((line, i) => {
    const trimmed = line.trim();
    // 跳过注释行（说明"为什么不能写 ?? 0"的注释里必然出现这个字面量）
    if (trimmed.startsWith("//") || trimmed.startsWith("*") || trimmed.startsWith("/*")) return;
    const re = /(?:\?\?|\|\|)\s*0(?:\.0+)?\b/g;
    for (const m of line.matchAll(re)) {
      const after = line.slice(m.index + m[0].length).trimStart();
      // 0 之后紧跟二元/比较运算符 → 这个 0 只参与判定，不产出展示值
      const isJudgementOnly = /^(?:[<>]=?|===?|!==?|\|\||&&)/.test(after);
      (isJudgementOnly ? softZero : fakeZero).push(`${file}:${i + 1}: ${trimmed}`);
    }
  });
  if (fakeZero.length === 0) {
    pass(`${file} 无「缺值兜成 0」的取值型写法（${why}）`);
  } else {
    fail(
      `${file} 出现把缺值兜成 0 的取值型写法（0 会被读成真实点位/平盘，属假数据；${why}）：\n      ` +
        fakeZero.join("\n      ")
    );
  }
  if (softZero.length > 0) {
    console.log(
      `  ⚠ ${file} 有 ${softZero.length} 处「折成 0 再比大小」的判定型写法（不算违规，但建议改严格比较）：\n      ` +
        softZero.join("\n      ")
    );
  }

  // B. 有 `?? null` 就必须有空值渲染分支
  const nullishFields = fields.filter((f) =>
    new RegExp(`(?:"${f}"|'${f}'|\\b${f})\\s*:\\s*[^,\\n]*\\?\\?\\s*null`).test(text) ||
    new RegExp(`\\b${f}\\s*=[^;\\n]*\\?\\?\\s*null`).test(text)
  );
  const hasNullBranch = /==\s*null|===\s*null/.test(text) || /["'`]—["'`]|["'`]--["'`]/.test(text);
  if (nullishFields.length === 0) {
    // 没有 `?? null` 不一定是错（例如该文件根本不兜底），但要显式说明，别静默跳过
    pass(`${file} 未使用 \`?? null\` 兜底（${why}）——本项无断言面`);
  } else if (hasNullBranch) {
    pass(
      `${file} 有 \`?? null\` 兜底且存在显式空值渲染（字段：${nullishFields.join("、")}；${why}）`
    );
  } else {
    fail(
      `${file} 用了 \`?? null\`（字段：${nullishFields.join("、")}）但**没有**空值渲染分支` +
        `（\`== null\` 判断或 —/-- 字面量）——视图会对 null 直接取值（${why}）`
    );
  }
}

if (failed) {
  console.log(`\n「不得假 0」门禁未通过：${failed} 项`);
}
process.exit(failed ? 1 : 0);
