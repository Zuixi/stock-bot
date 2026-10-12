// 门禁共用的 **AST 层**（Ruling 46）。
//
// 背景（为什么不再手写词法分析）：`check-no-fake-zero` 与 `check-design-tokens` 原先都在
// **源码原文**上跑正则，一轮里连出三个缺陷，根因是同一个决定：
//   ① 非空断言 `x!.toFixed(2)` 让"解引用"检测失效 → **假绿**（视图真的会炸，门禁却通过）；
//   ② fallback 按**变量名**记录，导致 A 处的 `var(--x, #fff)` 给 B 处的 `var(--x,)` 背书 → **假绿**
//      （正是 Ruling 45 在 F1 里否掉的「一处为另一处背书」）；
//   ③ 自制剥注释器不认识**含引号的正则字面量**（`/'/`）→ 吞掉文件余部 → 假绿 + 假红同时出现。
// 这三个不是三个 bug，是"手工 lexing"这一个决定的三个后果。
//
// `typescript` 已经是本仓库的**直接依赖**（`package.json` 的 `typescript`，Vite/tsc 本来就要），
// 故改用编译器 API 是**零新增依赖**：注释、字符串、正则、模板串、非空断言、可选链
// 全部由真解析器处理，"看不见的 `!`"在 AST 里是**另一种节点**，结构上不可能再被漏掉。
//
// 分工（按 Ruling 46 的指示）：
//   • `.ts/.tsx` → **一律走 AST**（本文件）；
//   • `.css`     → 保留文本解析（CSS 没有 JS 那套词法歧义；`/* */` 处理已有实测矩阵覆盖）。
//     CSS 侧只用 `source-scan.mjs` 里仍有价值的那部分，并在脚本头部写明这条分工与限度。

import ts from "typescript";

/** 解析一个 `.ts`/`.tsx` 源码为 AST（`ScriptKind` 按扩展名给，JSX 才不会解析失败）。 */
export function parseSource(fileName, text) {
  return ts.createSourceFile(
    fileName,
    text,
    ts.ScriptTarget.Latest,
    /* setParentNodes */ true,
    fileName.endsWith(".tsx") ? ts.ScriptKind.TSX : ts.ScriptKind.TS
  );
}

/** 深度优先遍历（含子节点）。 */
export function walk(node, visit) {
  visit(node);
  node.forEachChild((child) => {
    walk(child, visit);
  });
}

/** 节点文本（截断到一行，便于放进诊断信息）。 */
export function snippetOf(node, sf, max = 120) {
  const text = node.getText(sf).replace(/\s+/g, " ").trim();
  return text.length > max ? `${text.slice(0, max)}…` : text;
}

/** 1-based 行号。 */
export function lineOf(node, sf) {
  return sf.getLineAndCharacterOfPosition(node.getStart(sf)).line + 1;
}


/**
 * 把表达式降为"裸字段访问"：剥掉非空断言 `!`、括号、`as` 断言、可选链**标记**。
 *
 * 这是 NEW-1 的结构性修法：`index.change!.toFixed(2)` 的 receiver 是
 * `NonNullExpression(PropertyAccess(index.change))`，剥掉 `!` 后仍是 `index.change`；
 * 而旧文本正则因为中间多了个 `!` 而匹配不上 → 假绿。
 *
 * 注意：**不**剥可选链 `?.`——`index.change?.toFixed(2)` 本身是 null-safe 的，
 * 由调用方单独判断（见 `isOptionalChainSafe`）。
 */
export function unwrapExpression(node) {
  let current = node;
  for (;;) {
    if (ts.isParenthesizedExpression(current)) {
      current = current.expression;
      continue;
    }
    if (ts.isNonNullExpression(current)) {
      current = current.expression;
      continue;
    }
    if (ts.isAsExpression(current) || ts.isTypeAssertionExpression(current)) {
      current = current.expression;
      continue;
    }
    return current;
  }
}

/** 该访问/调用链上是否**出现过可选链**（`?.`）——出现过即 null-safe。 */
function isOptionalChainSafe(node) {
  let current = node;
  for (;;) {
    if (ts.isNonNullExpression(current) || ts.isParenthesizedExpression(current)) {
      current = current.expression;
      continue;
    }
    if (ts.isPropertyAccessExpression(current) || ts.isCallExpression(current)) {
      if (current.questionDotToken) return true;
      current = current.expression;
      continue;
    }
    return false;
  }
}

/**
 * 该节点是否是"**非空断言地解引用**某个可空字段"的访问点；命中返回**字段名**
 * （如 `change`），未命中返回 `undefined`。
 *
 * 注意返回的是**字段名**而不是 `getText()`：`getText()` 会带上非空断言（`index.change!`），
 * 且调用方需要拿**原始节点**去查祖先守卫（见 `isInNonNullBranch` 的 `!` 短路）。
 *
 * `fieldNames` = 字段名列表（`["value","change","changePercent"]`）。
 *
 * 判定规则（刻意做得简单可验证，替代前一版啰嗦的链式推导）：
 *   节点是 `PropertyAccessExpression`，其 **name 命中字段名**，且它**之上还有一层解引用**
 *   （父节点是取属性 / 取下标 / 调用），即"在字段之上又取了东西"。
 *
 * 这为什么能同时解决 NEW-1 与旧缺陷：
 *   • `index.change!.toFixed(2)`：内层 `index.change` 的父节点是 `PropertyAccessExpression`，
 *     而 `NonNullExpression` **不是** PropertyAccess —— 但它在**中间**层，所以从内层看父节点
 *     仍是 PropertyAccess（`index.change!` 的父是 `….toFixed`）。**`!` 是另一种节点，
 *     不可能像文本正则那样把它"跳过"而漏判**；
 *   • `index.change?.toFixed(2)`：父节点带 `questionDotToken` → 本来就是 null-safe，不算；
 *   • `(index.pctChange ?? 0) > 0`：`index.pctChange` 的父节点是 `??` 二元表达式 → 不是解引用；
 *   • `{ value: card.price ?? null }` 里的 `card.price`：父节点是 `??` → 不是解引用。
 */
export function dereferencedField(node, fieldNames) {
  const target = unwrapExpression(node);
  if (!ts.isPropertyAccessExpression(target)) return undefined;
  const fieldName = target.name.text;
  if (!fieldNames.includes(fieldName)) return undefined;

  // "字段被**求值**"的三种形态（少一种漏判、多一种误判——本轮两头都踩过）：
  //   1. **方法调用**：`index.change.toFixed(2)` → 父节点是 CallExpression 且 target 是被调者。
  //      这是真会炸的形态，也是本门禁的核心目标。
  //   2. **取下标**：`row.price[0]`。
  //   3. **非空断言后解引用**：`index.change!.toFixed(2)` → target 的父节点是
  //      NonNullExpression，其父才是取属性/调用。**NEW-1 正是这一种**：`!` 是独立节点，
  //      必须显式多剥一层，不能只看 target 的直接父节点。
  //
  // 明确**不**算解引用（逐一排除，避免误判）：
  //   • `{ value: index.value }` / JSX `someProp={index.value}` —— 只是**传值**；
  //   • `index.change?.toFixed(2)` —— 可选链本来就 null-safe；
  //   • `(index.change ?? 0)` —— 字段在 `??` 左侧，由 A 规则管。
  const parent = target.parent;
  if (!parent) return undefined;
  const asDerefBase = (p) => {
    if (ts.isPropertyAccessExpression(p) || ts.isCallExpression(p)) {
      if (p.questionDotToken) return false; // `?.` → 安全
      return unwrapExpression(p.expression) === target;
    }
    if (ts.isElementAccessExpression(p)) return unwrapExpression(p.expression) === target;
    return false;
  };
  if (asDerefBase(parent)) return fieldName;
  if (ts.isNonNullExpression(parent)) return fieldName; // `!` 抹掉 null：一律视为缺空值出口
  return undefined;
}

/**
 * 该节点是否处在"非空"分支里——即某个祖先把某个可空表达式的 **null 情形**排除了。
 *
 * 认两种形态（都是仓库里真实存在的写法）：
 *   1. 三元：`test ? A : B`，其中 `test` 里出现 `expr == null` / `expr === null`；
 *      若 `expr` 未取反，则 **B 分支**是"非空"分支；取反（`!= null`）则 **A 分支**。
 *   2. `&&` / `||` 逻辑链：`expr == null && <A>` → A 在"为空"分支；
 *      `expr != null && <A>` → A 在"非空"分支。
 *
 * 之所以按结构而不是按"文件里出现过 `== null`"：后者可以被**另一个字段**的守卫顶替
 * （Ruling 45 的 F1 实测：只删 `value` 的守卫，文件级断言被 `changePercent` 的守卫满足）。
 */
export function isInNonNullBranch(node, expressionText) {
  let current = node;
  let child = node;
  // `!` **声称**非空、并未检查非空：`index.change!.toFixed(2)` 恰恰是"视图会对 null 取值"
  // （NEW-1 的形态）。故先做一次向上短路：字段被非空断言包住 → 没有空值出口，直接 false。
  // （必须先把函数**提升**为声明式写法，才能在这里 return；见下方函数体。）
  const inner = unwrapExpression(node);
  const parent = inner.parent;
  if (parent && ts.isNonNullExpression(parent)) return false;

  while (current.parent) {
    const outer = current.parent;

    if (ts.isConditionalExpression(outer)) {
      const guarded = nullCheckOf(outer.condition.getText(), expressionText);
      if (guarded) {
        const inWhenTrue = outer.whenTrue === current || isAncestor(outer.whenTrue, current);
        const inWhenFalse = outer.whenFalse === current || isAncestor(outer.whenFalse, current);
        if (guarded.equalsNull && inWhenFalse) return true;
        if (guarded.notEqualsNull && inWhenTrue) return true;
      }
    }

    if (
      ts.isBinaryExpression(outer) &&
      outer.operatorToken.kind === ts.SyntaxKind.AmpersandAmpersandToken
    ) {
      const guarded = nullCheckOf(outer.left.getText(), expressionText);
      const inRight = outer.right === current || isAncestor(outer.right, current);
      if (inRight && guarded) {
        if (guarded.notEqualsNull) return true;
        if (guarded.equalsNull) return false;
      }
    }

    child = current;
    current = outer;
  }
  return false;
}

/** `current` 是否在 `root` 子树内。 */
function isAncestor(root, node) {
  let current = node;
  while (current) {
    if (current === root) return true;
    current = current.parent;
  }
  return false;
}

/**
 * 判断一段条件文本里是否含有"对 `expressionText` 的空值判断"。
 * 返回 `{ equalsNull, notEqualsNull }` 或 `undefined`。
 *
 * **不锚定首尾**（`^…$`）：真实代码里条件常是**嵌套三元**——
 * `a == null ? … : b == null ? … : …`，此时里层三元的 `condition.getText()`
 * 是整段 `b == null ? … : …`，锚定写法会判不出来（本轮实测的漏判原因）。
 * 只在本节点自己的条件文本里找，故仍满足"不接受别的字段/别处的守卫背书"。
 */
function nullCheckOf(testText, expressionText) {
  const expr = escapeRe(expressionText);
  const notEq = new RegExp(`${expr}\\s*!==?\\s*null`);
  const eq = new RegExp(`${expr}\\s*===?\\s*null`);
  if (notEq.test(testText)) return { equalsNull: false, notEqualsNull: true };
  if (eq.test(testText)) return { equalsNull: true, notEqualsNull: false };
  return undefined;
}

function escapeRe(s) {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}
