// 门禁共用的**注释感知**扫描原语。
//
// 为什么需要它（Ruling 45）：两个源码级门禁各自都栽在同一个坑上——**把注释当成代码**。
//   • 「不得假 0」门禁的"存在空值渲染分支"断言能被注释里的 `—` 满足 → **假绿**
//     （删掉真实的 `== null` 分支后门禁照样 PASS）；
//   • 设计令牌门禁会把注释里的 `--x: #fff;` 当成变量**定义** → 又一个假绿；
//     反过来，把注释里举例写的 `var(--x)` 当成**引用** → 假红。
// 结论：**任何源码级门禁都不得直接在原文上跑正则**。先剥注释，再匹配。
//
// 这里刻意**不引第三方解析器**（门禁要能在 `npm ci` 之前跑、且只依赖 node 内置模块）：
// 用一个小状态机逐字符走，正确地跳过 4 种字符串字面量与注释。它不解析语法树，
// 只保证"匹配到的东西不在注释里、也不在字符串内部被误切"。

/**
 * 剥掉 JS/TS/CSS 里的注释，**保留字符串字面量**与**行号**（把注释字符换成空格，
 * 换行原样保留），使"同一位置的匹配"在剥注释前后仍落在同一行。
 *
 * 处理的边界：
 * - `//` 行注释、块注释（斜杠星号 … 星号斜杠）；
 * - `'…'`、`"…"`、`` `…` `` 三种字符串（含反斜杠转义）；模板串里的 `${}` 不特殊处理
 *   ——门禁只关心 `?? 0` / `var(--x)` 这类片段，把它们当普通字符扫过即可；
 * - CSS 里没有 `//` 行注释，但把 `url(https://…)` 当字符串处理后会先进入 `"`/`'` 分支，
 *   裸 `//` 在 CSS 中不存在，故同一套状态机对 `.css/.ts/.tsx` 都安全。
 */
export function stripComments(source) {
  let out = "";
  let i = 0;
  const n = source.length;
  while (i < n) {
    const ch = source[i];
    const next = source[i + 1];
    // 行注释
    if (ch === "/" && next === "/") {
      while (i < n && source[i] !== "\n") {
        out += " ";
        i += 1;
      }
      continue;
    }
    // 块注释（保留其中的换行，避免行号漂移）
    if (ch === "/" && next === "*") {
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
    // 字符串字面量：原样保留（含引号与内容），只跳过转义
    if (ch === '"' || ch === "'" || ch === "`") {
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
    out += ch;
    i += 1;
  }
  return out;
}

/** 1-based 行号（配合 `matchAll` 的 `m.index` 用）。 */
export function lineOf(text, index) {
  let line = 1;
  for (let i = 0; i < index && i < text.length; i += 1) {
    if (text[i] === "\n") line += 1;
  }
  return line;
}

/**
 * 从 `var(` 之后扫出参数列表，正确跨过嵌套括号与字符串。
 *
 * 返回 `{ args, end }`：`args` 为按顶层逗号切分的原始参数文本；`end` 是匹配的 `)` 之后的下标。
 * 之所以要手写而不是正则：`var(--a, var(--b, #fff))` 有嵌套括号，正则会切错。
 */
export function readVarArgs(text, start) {
  const args = [];
  let depth = 0;
  let current = "";
  let i = start;
  for (; i < text.length; i += 1) {
    const ch = text[i];
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
  return { args, end: i };
}

/**
 * 解析出源码里所有 `var()` 引用。
 *
 * 返回 `[{ name, hasFallback, index }]`（`index` = `var(` 的下标）：
 * - `name` = **首参**里的自定义属性名；
 * - `hasFallback` = 首参之后**还存在非空参数**（即真的提供了兜底值）。
 *   注意 `var(--x,)` 这种"空兜底"按 CSS 语义**不算兜底**，故要求第二参去空格后非空。
 */
export function findVarRefs(text) {
  const refs = [];
  const re = /\bvar\(/g;
  for (const m of text.matchAll(re)) {
    const { args } = readVarArgs(text, m.index + m[0].length);
    const first = (args[0] ?? "").trim();
    const nameMatch = first.match(/^--[a-zA-Z0-9-]+/);
    if (!nameMatch) continue;
    // 关键：首个 `var(` 的参数里可能还嵌着别的 `var(`（如 `var(--sw-heat, var(--up))`），
    // 那个内部的 `var(` 会被本函数的 `matchAll` 自己扫到，故这里**只登记本层首参**，
    // 不递归——避免同一个变量被登记两次、也避免把兜底里的变量误标成"必填"。
    const hasFallback = args.slice(1).some((a) => a.trim().length > 0);
    refs.push({ name: nameMatch[0], hasFallback, index: m.index });
  }
  return refs;
}

/**
 * 判断某个 `?? 0` / `|| 0` 匹配之后是否**只用于判定**（不产出被使用的值）。
 *
 * 判定依据：跳过连续的收尾符号 `)`, `]`, `}` 与空白后，
 * - 若紧跟比较/逻辑运算符（`> < >= <= == === != !== && ||`）→ **判定型**；
 * - 否则 → **取值型**。
 *
 * 为什么必须跳过收尾括号（Ruling 45 / 探针 D）：`(x ?? 0) > 0` 里 `?? 0` 之后紧跟 `)`，
 * 旧实现直接看"下一个字符是不是运算符"，于是把判定型误判成取值型并**硬失败**——
 * 一段合法（虽然不理想）的选色代码把门禁打红，正是"假阳性逼人放宽规则"的成因。
 */
export function isJudgementOnly(text, matchEnd) {
  let i = matchEnd;
  while (i < text.length && /[\s)\]}]/.test(text[i])) i += 1;
  return /^(?:[<>]=?|===?|!==?|&&|\|\|)/.test(text.slice(i, i + 3));
}
