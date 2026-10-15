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
 * 手工词法正是 NEW-3 的成因。
 *
 * **必须跳过字符串**（Ruling 47 修的回归）：CSS 里 `content: "/*"`、
 * `url("https://ex.com/*")` 都是**字符串内容**，其中的 `/*` 不是注释起始符。
 * 上一版删掉了字符串跳过逻辑，于是 `content: "/*"` 会把**文件余部全部吞掉**——
 * 同时制造一次假绿（后面的引用全看不见）与一次假红（后面的定义也全看不见）。
 * 基准版 `stripComments` 本就处理对了这一点，本版按同样规则补回。
 *
 * 为什么 CSS 侧这样够了（不需要 JS 那套词法）：CSS 没有正则字面量、没有模板串、
 * 没有 `//` 行注释；字符串只有单引号与双引号两种，且转义只用反斜杠。
 * **另需整体跳过 `url(...)`**（见实现①）：CSS 允许 `url()` 里不加引号，而 URL 里
 * 完全可能出现 `/*`（如 `url(https://ex.com/*)`）——只跳字符串挡不住它。
 */
export function stripCssComments(source) {
  let out = "";
  let i = 0;
  const n = source.length;
  while (i < n) {
    const ch = source[i];

    // ① `url(` —— **整体当不透明单元跳过**（含未加引号的 URL）。
    //    为什么必须单独处理：CSS 允许 `url()` 里**不加引号**，而 URL 里完全可能出现 `/*`
    //    （实测 `url(https://ex.com/*)`）。只靠"跳过引号字符串"挡不住它 →
    //    整份文件余部被当注释吞掉 → 引用看不见（假绿）+ 定义看不见（假红）。
    //    与 ② 是**同一类**缺陷（把非注释的 `/*` 当注释），故一并堵住。
    //    在 `url(...)` 内只认**配对的 `)`**（允许一层嵌套括号）与其中的引号字符串；
    //    未闭合时一路走到文件尾——这种情况 CSS 本身已非法，交给下游解析器报错。
    if ((ch === "u" || ch === "U") && /^url\(/i.test(source.slice(i, i + 4))) {
      out += source.slice(i, i + 4);
      i += 4;
      let depth = 1;
      let quote = null;
      while (i < n && depth > 0) {
        const c = source[i];
        if (quote) {
          out += c;
          if (c === "\\") {
            out += source[i + 1] ?? "";
            i += 2;
            continue;
          }
          if (c === quote) quote = null;
          i += 1;
          continue;
        }
        if (c === '"' || c === "'") {
          quote = c;
        } else if (c === "(") {
          depth += 1;
        } else if (c === ")") {
          depth -= 1;
        }
        out += c;
        i += 1;
      }
      continue;
    }

    // ② 引号字符串：原样保留（含引号与内容），只跳过反斜杠转义——字符串里的 `/*` 不是注释
    if (ch === '"' || ch === "'") {
      const quote = ch;
      out += ch;
      i += 1;
      while (i < n) {
        if (source[i] === "\\") {
          out += source[i] + (source[i + 1] ?? "");
          i += 2;
          continue;
        }
        out += source[i];
        if (source[i] === quote) {
          i += 1;
          break;
        }
        i += 1;
      }
      continue;
    }

    // ③ 真正的块注释
    if (ch === "/" && source[i + 1] === "*") {
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

    out += ch;
    i += 1;
  }
  return out;
}

/** 1-based 行号（诊断信息用；`index` 为字符串下标）。 */
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
function readVarArgs(text, start) {
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
