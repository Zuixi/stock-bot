// 原始 ISO 时间戳门禁（Phase 0.6 / 计划 §8.2）。
//
// 依据：2026-10-13 全站设计评审实测「日期四种写法」——`10月9日` / `2026-10-12T15:00:00Z` /
// `1 天前` / `4 天前` 在同一产品里并存，其中 `2026-10-12T15:00:00Z` 是**机器串直接上屏**，
// 人读不出"这是收盘还是盘中"。口径统一走 `shared/ui/date.ts`（Phase 2 的 3.9 落 `formatAsOf`）。
//
// 做法（与 `check-design-tokens.mjs` 同一套 AST 纪律，见 `lib/ast-scan.mjs` 的文件头）：
//   • **只扫 `.tsx`**（契约如此）：命中对象是 JSX 文本节点与模板/字符串字面量；
//   • 用 TypeScript 编译器 API 遍历，故**注释与正则字面量不会误报**，字符串里的
//     "看起来像模板串的文本"也只是普通字符串内容；
//   • 解析失败**响亮报红**（`parseSource` 的 onError），不静默把一个文件移出视野——
//     否则一个未闭合模板串就能让整个门禁绿掉（这正是 `ast-scan.mjs` 记录的旧缺陷）。
//
// ⚠️ 本门禁的**明确限度**（写出来而不是静默）：它抓的是**源码里的字面量**。
// `{index.asof}` 这类"值来自后端 payload"的渲染不在静态扫描能力范围内——那层由
// `e2e/noRawIso.spec.ts`（运行时页面文本扫描，Phase 0 同批新增）兜。两层各守各的时期：
// 静态层防"有人把 ISO 串写死进 JSX"，运行时层防"后端给了 ISO、前端原样上屏"。
import { readFileSync, readdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

import ts from "typescript";

import { parseSource, walk } from "./lib/ast-scan.mjs";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const SRC = join(root, "src");

/** 契约模式（计划 §8.2 逐字）：`\d{4}-\d{2}-\d{2}T\d{2}:\d{2}` */
const ISO_PATTERN = /\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/;

let failed = 0;
const fail = (msg) => {
  console.log(`FAIL ${msg}`);
  failed += 1;
};

const walkFiles = (dir) => {
  const out = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = join(dir, entry.name);
    if (entry.isDirectory()) out.push(...walkFiles(full));
    else if (entry.name.endsWith(".tsx")) out.push(full);
  }
  return out;
};

const files = walkFiles(SRC);
let hits = 0;
const scannedStrings = { count: 0 };

for (const file of files) {
  const rel = file.slice(root.length + 1);
  const raw = readFileSync(file, "utf8");
  let sf;
  try {
    sf = parseSource(file, raw, (message) => {
      fail(`${message} —— 该文件已从本门禁视野里消失，必须修好语法才能被校验`);
    });
  } catch (err) {
    fail(`${rel} 解析失败：${err.message}`);
    continue;
  }

  const lineOf = (pos) => sf.getLineAndCharacterOfPosition(pos).line + 1;
  /** 一段"会被渲染/拼接的文本"是否含 ISO 串；`pos` 用于报行号 */
  const checkText = (text, pos, kind) => {
    scannedStrings.count += 1;
    if (!ISO_PATTERN.test(text)) return;
    // 命中位置的行号按段内偏移折算：段内可能有换行（JSX 文本节点常见）
    const offset = text.search(ISO_PATTERN);
    const prefix = text.slice(0, offset);
    const line = lineOf(pos) + (prefix.match(/\n/g)?.length ?? 0);
    fail(
      `${rel}:${line} ${kind} 含原始 ISO 时间戳「${text.slice(offset, offset + 16)}」` +
        ` —— 日期一律走 shared/ui/date.ts 的格式化入口，禁止机器串上屏`
    );
    hits += 1;
  };

  walk(sf, (node) => {
    if (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) {
      checkText(node.text, node.getStart(sf), "字符串字面量");
      return;
    }
    if (ts.isTemplateExpression(node)) {
      const spans = [node.head, ...node.templateSpans.map((s) => s.literal)];
      for (const span of spans) checkText(span.text, span.getStart(sf), "模板串片段");
      return;
    }
    if (ts.isJsxText(node)) {
      checkText(node.text, node.getStart(sf), "JSX 文本节点");
    }
  });
}

if (hits === 0) {
  console.log(
    `PASS 未发现源码写死的原始 ISO 时间戳（扫描 ${files.length} 个 .tsx / ${scannedStrings.count} 段文本；` +
      `注释与正则由 AST 排除）`
  );
}
console.log(
  `\n提示：本门禁只覆盖**源码字面量**。后端 payload 里的 ISO（如 {index.asof} 直出）由 ` +
    `e2e/noRawIso.spec.ts 在运行时页面文本上扫描。`
);

if (failed) {
  console.log(`\n原始 ISO 时间戳门禁未通过：${failed} 处`);
  process.exit(1);
}
process.exit(0);
