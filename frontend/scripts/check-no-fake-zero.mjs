// 「不得假 0」门禁（Ruling 44 / 45）。
//
// 为什么需要一条机械门禁：本仓库反复出现同一种错——**"没有数据"被渲染成 `0`**。
// `0` 在一个数值型页面上是一个**合法值**（平盘/点位 0），所以它比空白更危险：
// 用户与下游都读不出"其实是没取到"。同一类已经踩过三次：
//   ① 全球卡片改造前 `price`/`change` 曾用 `?? 0`（Task 7 段 A 改为 `null` + `price_source`）；
//   ② 详情页 `/index/<tsCode>` 的 globalCards 兜底对象写 `card.price ?? 0`（Ruling 44 修）；
//   ③ 新页 `/market/global` 的 `fmtPrice` 若写 `v ?? 0` 会同样撒谎。
//
// ── Ruling 45：三条"门禁自身"的纪律（每条都由实测缺陷换来）──────────────────
//   A. **不许在原文上跑正则** → 用 `lib/source-scan.mjs` 的 `stripComments`。
//      缺陷实证：删掉真实的 `== null` 分支后门禁仍 PASS——因为"存在空值渲染"的断言
//      被**注释里**的 `—` 满足了（假绿）。
//   B. **判定型与取值型必须区分正确** → `isJudgementOnly` 跳过收尾括号。
//      缺陷实证：`(x ?? 0) > 0` 被判成取值型并**硬失败**（假红），而文件头却声称只告警。
//   C. **扫描面不许是手工 allowlist** → 见下"扫描面"一节。
//
// ── 扫描面（Ruling 43 的方法；Ruling 45-C 要求）────────────────────────────
// **默认扫描 `src/` 下所有 `.ts/.tsx`**（不是三个文件的 allowlist）：新增页面若写了
// `?? 0` 也一定会被看到。但**判定标准是分级的**，这是有意设计、在此写明：
//   • **取值型 + 在展示面**：失败。`WATCHED` 是"用户直接看到行情数值"的那几个文件。
//   • **取值型 + 不在展示面**：告警。全库实测 ~30+ 处（zod/DTO 的 `volume: x ?? 0`、
//     数值聚合、排序键）。把它们全部判红 = 门禁立刻被关掉或被塞进几十条 allowlist，
//     等于没有门禁（Ruling 45 的假阳性教训）。
//   • **判定型**（`(x ?? 0) > 0` 之类"折成 0 再比大小"）：告警。它不产出展示值。
//   • **B 规则**（有 `?? null` 就必须有空值渲染分支）只在 `WATCHED` 上做——那里才有该契约。
// `WATCHED` 仍是 allowlist，故有**一条自检**：文件不存在即失败，防止重命名后门禁静默失去作用面。
//
// 破坏验证（见 report / 提交信息）：
//   ① 删掉 `market-index/index.tsx` 的真实 `== null` 分支 → **红**（F1）；
//   ② 把 `value: card.price ?? null` 改回 `?? 0` → **红**（F4 回归）；
//   ③ 把 `(pctChange ?? 0) > 0` 放回 `GlobalIndexCardView` → **通过**（判定型，仅告警）（F3）。

import { readFileSync, readdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

import { isJudgementOnly, stripComments } from "./lib/source-scan.mjs";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");

/**
 * **展示面** allowlist：用户直接看到行情数值的地方。这里出现"取值型 `?? 0`"即失败。
 * 每项都要写清"为什么它是展示面"，否则下一个人不知道能不能往里加。
 */
const WATCHED = [
  {
    file: "src/pages/market-index/index.tsx",
    fields: ["value", "change", "changePercent"],
    why: "指数/全球标的详情页（globalCards 兜底对象，Ruling 44）",
    // `change`/`changePercent` 的 null 兜底在**组件内部**（`ChangeText` 的 null 分支渲染
    // `--`，见 `shared/ui/ChangeText.tsx`），守卫不在本文件里。这不是"放过"，
    // 而是 JSX 里"传入 null-safe 组件"这种真实形态：要求页面再写一遍守卫只会产出死代码。
    // 该信任面很窄（仅这两个字段），且组件侧有对应实现——改动 `ChangeText` 的 null 行为时，
    // 这里的注释与它必须一起改。
    nullSafeVia: { change: "ChangeText", changePercent: "ChangeText" },
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

const SRC = join(root, "src");
const walkFiles = (dir) => {
  const out = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = join(dir, entry.name);
    if (entry.isDirectory()) out.push(...walkFiles(full));
    else if (/\.(ts|tsx)$/.test(entry.name)) out.push(full);
  }
  return out;
};

const allFiles = walkFiles(SRC);
const watchedRel = new Set(WATCHED.map((w) => w.file));

// 自检：allowlist 里的文件必须真的存在，否则门禁会"看起来在跑、其实没有作用面"
const missingWatched = [];
for (const { file } of WATCHED) {
  try {
    readFileSync(join(root, file), "utf8");
  } catch {
    missingWatched.push(file);
    fail(
      `展示面文件不存在：${file} —— WATCHED 是手工 allowlist，改名后必须同步，否则门禁静默失效`
    );
  }
}

const zeroRe = /(?:\?\?|\|\|)\s*0(?:\.0+)?\b/g;
const warnings = [];

for (const file of allFiles) {
  const rel = file.slice(root.length + 1);
  // Ruling 45-A：先剥注释再匹配。注释里说明"为什么不能写 ?? 0"是常态；
  // 不剥注释时，这类注释既会变成违规，也会让 B 规则被注释满足（假绿）。
  const text = stripComments(readFileSync(file, "utf8"));
  const isWatched = watchedRel.has(rel);

  for (const m of text.matchAll(zeroRe)) {
    const line = text.slice(0, m.index).split("\n").length;
    const snippet = (text.split("\n")[line - 1] ?? "").trim();
    const judgement = isJudgementOnly(text, m.index + m[0].length);
    if (judgement) {
      warnings.push(`[判定型] ${rel}:${line}: ${snippet}`);
      continue;
    }
    if (isWatched) {
      fail(
        `展示面出现「把缺值兜成 0」的取值型写法（0 会被读成真实点位/平盘，属假数据）：` +
          `${rel}:${line}: ${snippet}`
      );
    } else {
      warnings.push(`[非展示面] ${rel}:${line}: ${snippet}`);
    }
  }
}

if (missingWatched.length === 0) {
  pass(
    `展示面（${WATCHED.length} 个文件）无「把缺值兜成 0」的取值型写法；全库扫描 ${allFiles.length} 个 .ts/.tsx（注释已剥离）`
  );
}

// 判定型 / 非展示面：告警不失败（但**要可见**——静默才是危险的）
if (warnings.length > 0) {
  console.log(
    `  ⚠ 另有 ${warnings.length} 处「折成 0 后只参与判定/算术，或落在非展示面文件」的写法，不失败：`
  );
  for (const w of warnings.slice(0, 10)) console.log(`      ${w}`);
  if (warnings.length > 10) {
    console.log(`      …（其余 ${warnings.length - 10} 处省略）`);
  }
}

// ── B 规则：展示面里"每个 `?? null` 的字段都必须有**它自己的**空值渲染分支" ────
//
// 三处收紧（前两处来自 Ruling 45-A 的实测缺陷，第三处是本轮的额外收紧）：
//   1. 只在**剥注释后的代码**里找分支——先前注释里的 `—` 就能满足它（假绿）；
//   2. **按字段逐个**判定，不再用"文件里出现过 `== null`"这种全局存在性断言
//      ——后者可以被**另一个字段**的守卫顶替（实测：把 `value` 的守卫删掉、
//      只留 `changePercent` 的守卫，文件级断言照样通过）；
//   3. 守卫必须**锚定到该字段名**（`index.value == null`、`row.price != null`…），
//      而不是任意 `== null`。
//
// 已知限度（写明而不静默）：本规则保证"每个可空字段都有**以它命名的**空值守卫"，
// 不保证该守卫真的**包住**了对应渲染位置（那需要 AST）。对本门禁要防的那类错误
// （"兜底成 null 了却直接取值"、"守卫被删掉"）已经足够。
const guardRe = (field, alias) =>
  new RegExp(
    `(?:\\b${alias}\\.${field}\\b|\\b${field}\\b)\\s*(?:==|===|!=|!==)\\s*null`
  );

for (const { file, fields, why, nullSafeVia = {} } of WATCHED) {
  let text;
  try {
    text = stripComments(readFileSync(join(root, file), "utf8"));
  } catch {
    continue; // 文件缺失已在上面 fail 过
  }
  const nullishFields = fields.filter(
    (f) =>
      new RegExp(`(?:"${f}"|'${f}'|\\b${f})\\s*:\\s*[^,\\n]*\\?\\?\\s*null`).test(text) ||
      new RegExp(`\\b${f}\\s*=[^;\\n]*\\?\\?\\s*null`).test(text)
  );
  if (nullishFields.length === 0) {
    pass(`${file} 未使用 \`?? null\` 兜底（${why}）——本项无断言面`);
    continue;
  }
  // 每个字段自己的守卫（试 `index.value == null`、`card.value == null`… 直到命中）
  const exempt = [];
  const unguarded = nullishFields.filter((f) => {
    if (guardRe(f, "[A-Za-z_$][\\w$]*").test(text)) return false;
    // 兜底：也接受裸字段名形式（`value == null`）——某些文件在解构后使用
    if (new RegExp(`\\b${f}\\b\\s*(?:==|===|!=|!==)\\s*null`).test(text)) return false;
    // 显式登记"null 兜底在组件内部"的字段：该信任面必须逐个写明，不接受通配。
    // 判定是"本文件里该字段不再被非空断言地解引用"——即所有出现处都在 JSX 的
    // 组件 prop 里（`<ChangeText value={index.change} …>`），**没有**再链 `toFixed` 之类。
    const via = nullSafeVia[f];
    if (via) {
      if (new RegExp(`<${via}\\b`).test(text)) {
        const deref = new RegExp(
          `(\\.${f}\\b|\\b${f}\\b)\\s*\\.\\s*(?:toFixed|toLocaleString|toPrecision)\\s*\\(`
        ).test(text);
        if (!deref) {
          exempt.push(`${f}（null 分支在 ${via} 内）`);
          return false;
        }
      }
    }
    return true;
  });
  if (unguarded.length === 0) {
    pass(
      `${file} 的每个 \`?? null\` 字段都有空值出口（本文件守卫或已登记的 null-safe 组件）：` +
        `${nullishFields.join("、")}${exempt.length ? `；其中 ${exempt.join("、")}` : ""}（${why}）`
    );
  } else {
    fail(
      `${file} 这些字段用 \`?? null\` 兜底但**既没有以它命名的空值守卫、也没有登记 null-safe 出口**` +
        `（代码里的；注释不算）：${unguarded.join("、")} —— 视图会对 null 直接取值（${why}）`
    );
  }
}

if (failed) {
  console.log(`\n「不得假 0」门禁未通过：${failed} 项`);
}
process.exit(failed ? 1 : 0);
