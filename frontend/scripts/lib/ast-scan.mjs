// 门禁共用的 **TypeScript 解析层**（Ruling 47 收敛后的形态）。
//
// 为什么用编译器 API：`.ts/.tsx` 的注释、字符串、正则、模板串由**真解析器**处理。
// 前一版在源码原文上跑正则（自制词法器），一轮内连出三类缺陷——含引号的正则字面量吞文件、
// 非空断言 `!` 让检测失效、注释既被当成证据又被当成定义。改用 AST 后这些**结构性消失**：
// `!`、注释、正则各自是独立节点/词法单位，不再有"看不见"这回事。
//
// 剩下的消费者只有 **CSS 变量门禁**（`check-design-tokens.mjs`）：
//   • `parseSource` 取出 `var(--x)` 所在的字符串字面量（`style={{ color: "var(--up)" }}`）
//     与内联样式键（`{"--x": "1px"}`）——**字符串内容是文本**，必须按文本解析 `var()`，
//     但只在 AST 给出的那一个字符串值上解析，不接触文件全文；
//   • `walk` 深度优先遍历上述节点。
//
// 被撤除的"假 0"门禁（Ruling 47）：它要回答的"这个值会不会被渲染"是**数据流**问题，
// 父节点形状判不出来，且会误红合法代码（如 `x && x.toFixed()`）。其"缺值不得渲染成 0"
// 的纪律改为：数据层契约（`price_source == "none"`，pytest 机械覆盖）+
// `best-practices/frontend.md` 的人工纪律。故本文件不再保留"解引用检测/空值分支检测"
// 那一套（已无消费者，属死代码）。
//
// ⚠️ **依赖 `node_modules`**：本层 `import ts from "typescript"`，故两个门禁都要求在
// `frontend/` 下、且已 `npm ci`（CI 的 `frontend-lint` job 先 `npm ci` 再跑门禁，与
// `check:design`/`check:routes` 同址）。未安装时是**响亮失败**（模块解析错误），不会静默跳过。

import ts from "typescript";

/**
 * 解析一个 `.ts`/`.tsx` 源码为 AST（`ScriptKind` 按扩展名给，JSX 才不会解析失败）。
 *
 * ⚠️ **解析错误必须响亮报出**（Ruling 47 修的缺陷）：`createSourceFile` 对语法错误
 * **不抛异常**，而是把诊断挂到 `sourceFile.parseDiagnostics` 上。忽略它的后果是
 * "解析器看不懂 → 什么都没找到 → 门禁绿" —— 一个**未闭合的模板串**就能让整个文件
 * 从门禁视野里消失（实测：`const s = \`…` 未闭合 + 一个真 `?? 0` → 旧版 exit 0）。
 * 这与本文件开头批判的"手工 lexing 吞文件"是**同一类失败**（静默失去作用面），
 * 所以必须由本函数自己兜住，而不是指望 CI 里另有 `tsc` 当backstop。
 *
 * `onError(message)` 由调用方注入（门禁各自决定怎么记账）；缺省则直接抛，
 * 让"没人处理"也变成响亮失败而不是静默通过。
 */
export function parseSource(fileName, text, onError) {
  const sf = ts.createSourceFile(
    fileName,
    text,
    ts.ScriptTarget.Latest,
    /* setParentNodes */ true,
    fileName.endsWith(".tsx") ? ts.ScriptKind.TSX : ts.ScriptKind.TS
  );
  // **失败关闭**（Ruling 48）：`parseDiagnostics` 是 TypeScript 的**内部**字段，
  // 不在 `typescript.d.ts` 里公开声明。写成 `?? []` 等于"字段一旦改名（TS 升级）就静默
  // 恢复成修复前的绿"——正是刚修掉的那个失败模式换个入口复活。
  // 一个看不见解析错误的门禁**不许报成功**，故字段缺失本身即失败（带上排查提示）。
  if (!("parseDiagnostics" in sf)) {
    const message =
      `${fileName} 无法读取解析诊断：本版 typescript 的 SourceFile 上没有 \`parseDiagnostics\` 字段` +
      `（它是内部字段，可能已随版本改名/移除）。本门禁必须看得见解析错误才敢报成功，` +
      `请改用公开 API（如 \`ts.createProgram\` + \`program.getSyntacticDiagnostics()\`）后重跑。`;
    if (typeof onError === "function") onError(message);
    else throw new Error(message);
    return sf;
  }
  const diags = sf.parseDiagnostics;
  if (diags.length > 0) {
    const first = diags[0];
    const { line, character } = sf.getLineAndCharacterOfPosition(first.start ?? 0);
    const message =
      `${fileName}:${line + 1}:${character + 1} 解析失败：` +
      ts.flattenDiagnosticMessageText(first.messageText, " ") +
      (diags.length > 1 ? `（另有 ${diags.length - 1} 条同类诊断）` : "");
    if (typeof onError === "function") onError(message);
    else throw new Error(message);
  }
  return sf;
}

/** 深度优先遍历（含子节点）。 */
export function walk(node, visit) {
  visit(node);
  node.forEachChild((child) => {
    walk(child, visit);
  });
}
