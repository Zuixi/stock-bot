// 门禁共用的**文本**解析原语（Ruling 46 之后只剩两件正事）。
//
// 分工（Ruling 46 明确要求）：
//   • `.ts/.tsx` 的**结构与词法** —— 一律交给 `lib/ast-scan.mjs`（TypeScript 编译器 API）。
//     这里不再承担"剥注释/找 token"的职责：自制词法器不认识含引号的正则字面量（`/'/`），
//     会把文件余部当字符串吞掉，一轮内直接造成假绿 + 假红（NEW-3）。
//   • `.css` 与 **字符串里的 `var(...)`** —— 保留文本解析。理由：CSS 没有 JS 那套词法歧义
//     （没有正则字面量、没有模板串；`/* */` 与字符串由 `stripCssComments` 处理，
//     已有实测矩阵覆盖），而 `var()` 是**字符串内容**，本来就不在 AST 结构里，必须按文本解析。
//     **限度**：这两条路径都只在"注释已剥离的 CSS"或"AST 给出的单个字符串字面量值"上工作，
//     不接触原始文件全文 —— 故 NEW-3 那一类无法复现。

/**
 * 剥掉 CSS 块注释（保留换行以维持行号）。
 *
 * ⚠️ **只用于 `.css` 文件**。不要再用它处理 `.ts/.tsx`：那条路已交给解析器，
 * 手工词法正是 NEW-3 的成因。CSS 侧之所以安全：CSS 没有正则字面量、没有 `//` 行注释
 * （`url(https://…)` 必须加引号；未加引号的 `url(...)` 里也不会出现块注释起始符）。
 */
export function stripCssComments(source) {
  let out = "";
  let i = 0;
  const n = source.length;
  while (i < n) {
    if (source[i] === "/" && source[i + 1] === "*") {
      out += "  ";
      i += 2;
      while (i < n && !(source[i] === "*" && source[i + 1] === "/")) {
        out += source[i] === "\n" ? "\n" : " ";
        i += 1;
      }
      if (i < n) {
        out += "  ";
        i += 2;
      }
      continue;
    }
    out += source[i];
    i += 1;
  }
  return out;
}

/** 1-based 行号。 */
export function lineOf(text, index) {
  let line = 1;
  for (let i = 0; i < index && i < text.length; i += 1) {
    if (text[i] === "\n") line += 1;
  }
  return line;
}

/**
 * 从 `var(` 之后扫出参数列表，正确跨过嵌套括号与引号。
 * 返回 `{ args, end }`：`args` 按顶层逗号切分；`end` 是配对 `)` 之后的下标。
 *
 * 手写而不是正则：`var(--a, var(--b, #fff))` 有嵌套括号，正则会切错。
 */
export function readVarArgs(text, start) {
  const args = [];
  let depth = 0;
  let current = "";
  let quote = null;
  for (let i = start; i < text.length; i += 1) {
    const ch = text[i];
    if (quote) {
      current += ch;
      if (ch === "\\") {
        current += text[i + 1] ?? "";
        i += 1;
      } else if (ch === quote) {
        quote = null;
      }
      continue;
    }
    if (ch === '"' || ch === "'") {
      quote = ch;
      current += ch;
      continue;
    }
    if (ch === "(") {
      depth += 1;
      current += ch;
      continue;
    }
    if (ch === ")") {
      if (depth === 0) {
        args.push(current);
        return { args, end: i + 1 };
      }
      depth -= 1;
      current += ch;
      continue;
    }
    if (ch === "," && depth === 0) {
      args.push(current);
      current = "";
      continue;
    }
    current += ch;
  }
  args.push(current);
  return { args, end: text.length };
}

/**
 * 解析出文本里所有 `var()` 引用（**逐引用**给出其自身的 fallback 事实）。
 *
 * 返回 `[{ name, hasFallback, index }]`：
 * - `name` = 首参里的自定义属性名；
 * - `hasFallback` = 首参之后**存在非空参数**（`var(--x,)` 的空兜底在 CSS 语义下**不算**兜底）。
 *
 * **`hasFallback` 必须逐引用判定**（NEW-2 的修法）：旧实现把"有兜底"的变量名记进一个
 * 全局集合，于是 A 处写了 `var(--x, #fff)` 就给 B 处的 `var(--x,)` 背书——
 * 正是 Ruling 45 在 F1 里否掉的「一处为另一处背书」。现在每个 `var(...)` 自己决定自己。
 */
export function findVarRefs(text) {
  const refs = [];
  for (const m of text.matchAll(/\bvar\(/g)) {
    const { args } = readVarArgs(text, m.index + m[0].length);
    const first = (args[0] ?? "").trim();
    const nameMatch = first.match(/^--[a-zA-Z0-9-]+/);
    if (!nameMatch) continue;
    // 嵌套的 `var(` 会被本函数的 matchAll 自己扫到，故这里只登记本层首参，不递归
    const hasFallback = args.slice(1).some((a) => a.trim().length > 0);
    refs.push({ name: nameMatch[0], hasFallback, index: m.index });
  }
  return refs;
}
