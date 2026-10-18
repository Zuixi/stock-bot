// 前端字段 ⊆ 后端 schema 契约门禁（P0-6，**永久检查**）。
//
// 为什么要有它（F7 / F1 的真因）：概览页曾读 `stock.roe` / `stock.revenueGrowth` /
// `stock.profitGrowth` —— 后端响应里**从来没有**这三个字段。TS 抓不到，因为它们被声明成
// `roe?: number`（可选），于是页面把 `--` 静默渲染了数月，直到人工发现。根因不是某个人粗心，
// 而是**「前端声明的字段 ⊆ 后端 schema 的字段」这条契约没有任何机械门禁**。
//
// 断言（**单向**，只此一条）：
//   `StockRecord` 声明的每个字段，都必须能在后端 `StockOut` ∪ `StockEnrichedOut`
//   （含 `StockOut` 的继承字段）里找到对应的 backend 字段名。
//
// **故意不断言反向**：后端 `StockOut` 有一堆前端合法忽略的字段（`enname`/`is_hs`/`delist_date`…），
// 断言"后端字段必须被前端声明"会把正常的后端演进当成红灯。
//
// 字段映射的权威来源 = **`src/shared/api/stocks.ts` 里真实的 mapper**（`mapBackendStock` /
// `mapBackendStockEnriched`）。那是唯一"后端字段名 → 前端字段名"的落地翻译，比任何手抄清单权威：
// mapper 改了而本脚本没改，脚本会跟着真实代码走（若 mapper 与解析不符则**响亮报错**，见下）。
// 命名不规则的映射（`pe` → `pe_ttm`、`marketCap` → `total_mv` …）必须在 `IRREGULAR_MAPPINGS`
// 里写一行理由；mapper 新增一条不规则映射却没登记理由 → 红（防"没人看得懂的映射"）。
//
// 例外：`ALLOWED_MISSING` 是"前端允许声明、后端确实没有"的显式清单，**每项必须写一行理由**，
// 当前为**空**——P0-6 的目标就是零例外。加豁免是最后手段，不是逃生门。
//
// 破坏验证（每一步都必须转红，见 P0-6-report.md）：
//   1. 给 `StockRecord` 加 `dividendYield?: number` → 红，点名 dividendYield；
//   2. 复现历史 bug 形状：加 `roe?: number` → 红，点名 roe；
//   3. 改 mapper 里任一 `item.<后端字段>` 为不存在的名字 → 红（映射跟真实代码）；
//   4. mapper 新增一条不规则映射但不写理由 → 红；
//   5. 让 `StockEnrichedOut` 不再继承 `StockOut` 且删掉某个继承字段 → 红（证明继承字段真的并进来了）。

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const REPO = join(root, "..");
const SCHEMA_PY = join(REPO, "backend/app/schemas/stock.py");
const TYPES_TS = join(root, "src/shared/types/index.ts");
const API_TS = join(root, "src/shared/api/stocks.ts");

const PLAN_REF = "plans/2026-10-19-stock-detail-overhaul.md P0-6";

let failed = 0;
const fail = (msg) => {
  console.log(`FAIL ${msg}`);
  failed += 1;
};
const pass = (msg) => console.log(`PASS ${msg}`);

function readOrFail(path) {
  try {
    return readFileSync(path, "utf8");
  } catch (err) {
    fail(`读不到 ${path}（${err.message}）—— 字段契约门禁无法执行`);
    return null;
  }
}

/**
 * `StockRecord` 允许声明、但后端确实没有的字段。**加一项必须写一行理由**（空理由直接红）。
 * 当前为**空**：P0-6 的验收就是零例外（例外一多，门禁就退化成摆设）。
 * @type {Map<string, string>}
 */
const ALLOWED_MISSING = new Map([
  // 例：["someField", "为什么后端没有却必须在前端声明（一句话，说清来源与用途）"],
]);

/**
 * 命名不规则的映射：前端名 ≠ camelCase→snake_case 的结果。**每一项都必须写理由**。
 * key = StockRecord 字段名；backend = mapper 真实映射到的后端字段名。
 * @type {Record<string, { backend: string, reason: string }>}
 */
const IRREGULAR_MAPPINGS = {
  industry: {
    backend: "csrc_desc",
    reason: "前端 industry 取后端 csrc_desc（名录里的行业描述）；后端同名字段 industry 是另一口径，勿混",
  },
  turnover: {
    backend: "amount",
    reason: "前端 turnover 是**成交额**（元，由后端 amount 千元 ×1e3 换算），换手率另有 turnoverRate",
  },
  marketCap: {
    backend: "total_mv",
    reason: "前端 marketCap ↔ 后端 TuShare 口径 total_mv（万元，mapper 换算为元）",
  },
  circulatingCap: {
    backend: "circ_mv",
    reason: "前端 circulatingCap ↔ 后端 TuShare 口径 circ_mv（万元，mapper 换算为元）",
  },
  pe: {
    backend: "pe_ttm",
    reason: "前端短名 pe 实为滚动市盈率 pe_ttm，后端无裸 pe 字段（`pe`/`pe_ttm` 都查过，绝非漏映射）",
  },
};

// ── 通用小工具 ──────────────────────────────────────────────────────────────

/** camelCase → snake_case（确定性；不处理不规则映射——那些走 IRREGULAR_MAPPINGS） */
function camelToSnake(name) {
  return name
    .replace(/([a-z0-9])([A-Z])/g, "$1_$2")
    .replace(/([A-Z]+)([A-Z][a-z])/g, "$1_$2")
    .toLowerCase();
}

/** 统计子串出现次数（三引号 / 括号配平用） */
function countOf(text, token) {
  let n = 0;
  let i = text.indexOf(token);
  while (i !== -1) {
    n += 1;
    i = text.indexOf(token, i + token.length);
  }
  return n;
}

/**
 * 从 `open` 位置的 `{` 起做括号配平，返回闭合 `}` 的下标（找不到返回 -1）。
 * 输入是这两个 TS/Python 文件的**声明区**（对象字面量 / class body），不含模板串里的括号；
 * 配不平就返回 -1，由调用方**响亮报错**，绝不静默当成空集合。
 */
function matchBrace(text, open) {
  let depth = 0;
  for (let i = open; i < text.length; i += 1) {
    const ch = text[i];
    if (ch === "{") depth += 1;
    else if (ch === "}") {
      depth -= 1;
      if (depth === 0) return i;
    }
  }
  return -1;
}

// ── 1. 后端：解析 Pydantic 模型（`StockOut` / `StockEnrichedOut`）──────────────

/**
 * 解析文件里所有顶层 `class X(Base):` 的字段名（只认 `name: type` 形态）。
 *
 * 要跳过的坑（逐个都有真实理由，别简化）：
 *   - `model_config = {...}`：没有 `name:` 形态，天然不匹配；仍显式跳过以免将来加类型注解；
 *   - 方法：`def foo(...)` 可能**跨行**，故进入 def 后按括号配平跳过整个签名；
 *   - 注释 `# ...` 与单行/多行 docstring（三引号）；
 *   - `class` 行与装饰器 `@...`；
 *   - `Field(\n  description="..."\n)` 的续行：只有 `key=value`，不是 `name: type`。
 * @returns {Map<string, { bases: string[], fields: string[] }>}
 */
function parsePydanticModels(source) {
  const lines = source.split("\n");
  const models = new Map();
  for (let i = 0; i < lines.length; i += 1) {
    const head = lines[i].match(/^class\s+([A-Za-z_]\w*)\s*(?:\(([^)]*)\))?\s*:/);
    if (!head) continue;
    const name = head[1];
    const bases = (head[2] ?? "")
      .split(",")
      .map((b) => b.trim().split(".").pop().trim())
      .filter(Boolean);
    const fields = [];
    let inDoc = false;
    let defDepth = 0; // >0 表示正处在跨行 `def` 签名里
    for (let j = i + 1; j < lines.length; j += 1) {
      const line = lines[j];
      // 列 0 的非空行 = 回到顶层 → 本类结束
      if (line.trim() !== "" && !/^\s/.test(line)) break;
      const trimmed = line.trim();
      if (defDepth > 0) {
        defDepth += countOf(line, "(") - countOf(line, ")");
        if (defDepth <= 0) defDepth = 0;
        continue;
      }
      if (trimmed === "") continue;
      if (inDoc) {
        if (countOf(line, '"""') % 2 === 1 || countOf(line, "'''") % 2 === 1) inDoc = false;
        continue;
      }
      if (/^#/.test(trimmed) || /^@/.test(trimmed)) continue;
      if (/^class\s/.test(trimmed)) continue; // 嵌套类不递归（本仓库 schema 不用）
      if (countOf(line, '"""') % 2 === 1 && !/^""".*"""$/.test(trimmed)) {
        inDoc = true;
        continue;
      }
      if (/^"""/.test(trimmed) || /^'''/.test(trimmed)) continue; // 单行 docstring
      if (/^(async\s+)?def\s/.test(trimmed)) {
        defDepth = Math.max(0, countOf(line, "(") - countOf(line, ")"));
        continue;
      }
      if (/^model_config\b/.test(trimmed)) continue;
      // 字段行：`name: type`（可有 `= Field(...)` 默认值）；必须小写/下划线开头
      const field = trimmed.match(/^([a-z_][A-Za-z0-9_]*)\s*:\s*\S/);
      if (field) fields.push(field[1]);
      else if (countOf(line, '"""') % 2 === 1) inDoc = true; // `"""` 开头的多行 docstring
    }
    models.set(name, { bases, fields });
    i += 0; // 类体由内层循环消费，外层继续（遇到下一个 class 会重新匹配）
  }
  return models;
}

/** 递归收集某模型及其祖先的字段（`StockEnrichedOut(StockOut)` 必须并进 StockOut 的字段） */
function collectFields(models, name, seen = new Set()) {
  if (seen.has(name)) return [];
  seen.add(name);
  const model = models.get(name);
  if (!model) return [];
  const inherited = model.bases.flatMap((base) => collectFields(models, base, seen));
  return [...inherited, ...model.fields];
}

// ── 2. 前端：解析 `StockRecord` 字段 ─────────────────────────────────────────

/** `export interface StockRecord { ... }` 的字段名（跳过注释行；方法 `foo(): T` 不会被 `name:` 命中） */
function parseStockRecord(source) {
  const head = source.match(/export\s+interface\s+StockRecord\s*(?:extends[^{]*)?\{/);
  if (!head) return null;
  const open = head.index + head[0].length - 1;
  const close = matchBrace(source, open);
  if (close === -1) return null;
  const body = source.slice(open + 1, close);
  const fields = [];
  for (const line of body.split("\n")) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("//") || trimmed.startsWith("/*") || trimmed.startsWith("*")) {
      continue;
    }
    const m = trimmed.match(/^([A-Za-z_$][\w$]*)\s*\??\s*:/);
    if (m) fields.push(m[1]);
  }
  return fields;
}

// ── 3. 权威映射：从真实 mapper 派生「前端字段 → 后端字段」 ─────────────────────

/**
 * 从 `src/shared/api/stocks.ts` 的 `function <fnName>(item) { ... }` 里抽
 * `前端字段: <右侧含 item.<后端字段>>` 的行，得到 field pairs。
 *
 * 输入形态是本仓库既有写法（每条映射一行）：`latestPrice: item.latest_price ?? undefined,`
 * 与 `turnover: item.amount == null ? undefined : item.amount * 1e3,` —— 取右侧**第一个**
 * `item.<name>`。解析不到的字段会退回 camelCase 兜底（不静默放行：不规则映射会因此转红）。
 * 若整个函数解析出 0 条映射 → 调用方**响亮报错**（绝不退化成"空映射 + 全绿"）。
 * @returns {Map<string, string>}
 */
function parseMapperPairs(source, fnName) {
  const head = source.match(new RegExp(`function\\s+${fnName}\\s*\\(`));
  if (!head) return null;
  const open = source.indexOf("{", head.index);
  if (open === -1) return null;
  const close = matchBrace(source, open);
  if (close === -1) return null;
  const pairs = new Map();
  for (const rawLine of source.slice(open + 1, close).split("\n")) {
    const line = rawLine.replace(/\s*\/\/.*$/, ""); // 去掉行尾注释（如 "// 手：..."）
    const m = line.match(/^\s*([A-Za-z_$][\w$]*)\s*:\s*(.+)$/);
    if (!m) continue;
    const rhs = m[2].replace(/,\s*$/, "");
    const backend = rhs.match(/item\.([A-Za-z_]\w*)/);
    if (backend) pairs.set(m[1], backend[1]);
  }
  return pairs;
}

// ── 主流程 ──────────────────────────────────────────────────────────────────

const schemaSource = readOrFail(SCHEMA_PY);
const typesSource = readOrFail(TYPES_TS);
const apiSource = readOrFail(API_TS);
if (failed) {
  console.log(`\n字段契约门禁未通过：${failed} 项`);
  process.exit(1);
}

const models = parsePydanticModels(schemaSource);
const requiredModels = ["StockOut", "StockEnrichedOut"];
const missingModels = requiredModels.filter((m) => !models.has(m));
if (missingModels.length) {
  fail(`后端 ${SCHEMA_PY} 里找不到模型：${missingModels.join("、")} —— 请同步本脚本与真实 schema`);
}
if (models.has("StockOut") && models.get("StockOut").fields.length === 0) {
  fail("解析 StockOut 得到 0 个字段 —— 解析器与真实 schema 形态不符（不许静默当空集）");
}
if (models.has("StockEnrichedOut") && models.get("StockEnrichedOut").fields.length === 0) {
  fail("解析 StockEnrichedOut 得到 0 个字段 —— 解析器与真实 schema 形态不符（不许静默当空集）");
}
if (failed) {
  console.log(`\n字段契约门禁未通过：${failed} 项`);
  process.exit(1);
}

const stockOutFields = collectFields(models, "StockOut");
const enrichedAllFields = collectFields(models, "StockEnrichedOut");
const backendFields = new Set([...stockOutFields, ...enrichedAllFields]);
if (enrichedAllFields.length <= stockOutFields.length) {
  fail(
    `StockEnrichedOut 未并进继承字段（StockOut ${stockOutFields.length} 个 / StockEnrichedOut 含继承共 ` +
      `${enrichedAllFields.length} 个）—— 继承链断了，检查 class 声明`
  );
}
pass(
  `后端字段集：StockOut ${stockOutFields.length} + StockEnrichedOut 自有 ` +
    `${models.get("StockEnrichedOut").fields.length}（含继承共 ${enrichedAllFields.length}，去重 ${backendFields.size}）`
);
pass(`StockEnrichedOut 继承 StockOut 的字段已并入后端字段集（继承链 ${models.get("StockEnrichedOut").bases.join(" → ")}）`);

const recordFields = parseStockRecord(typesSource);
if (!recordFields) {
  fail(`解析不到 ${TYPES_TS} 的 \`export interface StockRecord { ... }\` —— 请同步本脚本与类型声明`);
} else if (recordFields.length === 0) {
  fail("解析 StockRecord 得到 0 个字段 —— 解析器与真实类型声明形态不符（不许静默当空集）");
}
if (failed) {
  console.log(`\n字段契约门禁未通过：${failed} 项`);
  process.exit(1);
}

const basePairs = parseMapperPairs(apiSource, "mapBackendStock");
const enrichedPairs = parseMapperPairs(apiSource, "mapBackendStockEnriched");
if (!basePairs || basePairs.size === 0 || !enrichedPairs || enrichedPairs.size === 0) {
  fail(
    "mapper 解析失败：`mapBackendStock` / `mapBackendStockEnriched` 里抽不到任何 `前端字段: item.<后端字段>`" +
      " —— 字段映射的权威来源不可读，门禁无法判定（保持单行映射写法，勿静默降级）"
  );
  console.log(`\n字段契约门禁未通过：${failed} 项`);
  process.exit(1);
}
/** 合并两个 mapper：enriched 覆盖 base（同名字段以后者为准，因为它才是最终返回给页面的值） */
const mapperPairs = new Map([...basePairs, ...enrichedPairs]);
pass(
  `映射来源 = src/shared/api/stocks.ts（mapBackendStock ${basePairs.size} 条 + ` +
    `mapBackendStockEnriched ${enrichedPairs.size} 条，合并 ${mapperPairs.size} 条）`
);

// 3b. 命名不规则的映射必须写理由；理由过期（不再成立）也必须删
const actualIrregular = [...mapperPairs].filter(([field, backend]) => backend !== camelToSnake(field));
const undocumented = actualIrregular.filter(([field]) => !IRREGULAR_MAPPINGS[field]);
if (undocumented.length === 0) {
  pass(`mapper 的 ${actualIrregular.length} 条非 camelCase 映射全部有理由（IRREGULAR_MAPPINGS）`);
} else {
  fail(
    `mapper 里有非 camelCase 映射但没写理由：` +
      `${undocumented.map(([f, b]) => `${f}→${b}`).join("、")} —— 在 check-api-fields.mjs 的 IRREGULAR_MAPPINGS 补一行理由`
  );
}
const staleDocs = Object.entries(IRREGULAR_MAPPINGS).filter(([field, doc]) => {
  const actual = mapperPairs.get(field);
  return actual !== doc.backend;
});
if (staleDocs.length === 0) {
  pass("IRREGULAR_MAPPINGS 与真实 mapper 逐条一致（无过期理由）");
} else {
  fail(
    "IRREGULAR_MAPPINGS 与真实 mapper 不符（过期条目必须删除/修正）：" +
      staleDocs.map(([f, d]) => `${f} 记为 ${d.backend}，mapper 实为 ${mapperPairs.get(f) ?? "未映射"}`).join("；")
  );
}
const emptyReason = actualIrregular.filter(([field]) => {
  const doc = IRREGULAR_MAPPINGS[field];
  return doc && !String(doc.reason ?? "").trim();
});
if (emptyReason.length === 0) pass("IRREGULAR_MAPPINGS 每条都写了理由（非空）");
else fail(`IRREGULAR_MAPPINGS 有条目缺理由：${emptyReason.map(([f]) => f).join("、")}`);

// 3c. 例外清单自检：必须写理由、且不得过期（过期豁免会让门禁悄悄变松）
const emptyAllow = [...ALLOWED_MISSING].filter(([, reason]) => !String(reason ?? "").trim());
if (emptyAllow.length === 0) pass(`ALLOWED_MISSING 例外 ${ALLOWED_MISSING.size} 项，每项都有理由`);
else fail(`ALLOWED_MISSING 有条目缺理由：${emptyAllow.map(([f]) => f).join("、")}`);
const unknownAllow = [...ALLOWED_MISSING.keys()].filter((f) => !recordFields.includes(f));
if (unknownAllow.length === 0) pass("ALLOWED_MISSING 无幽灵条目（每个 key 都真的声明在 StockRecord 里）");
else fail(`ALLOWED_MISSING 有过期条目（StockRecord 已无此字段）：${unknownAllow.join("、")}`);

// ── 4. 核心断言：StockRecord 的每个字段都必须落到后端字段集 ────────────────────

/**
 * 一个前端字段可以被哪些后端字段满足（按优先级）：
 *   ① mapper 真实映射到的字段（权威）；
 *   ② camelCase→snake_case 的确定性结果；
 *   ③ ② + `_ttm`（后端常用 `_ttm` 后缀表达滚动口径，如 `pe_ttm`；报错信息必须把两个候选都列出来）。
 */
function candidatesFor(field) {
  const snake = camelToSnake(field);
  const mapped = mapperPairs.get(field);
  return [...new Set([mapped, snake, `${snake}_ttm`].filter(Boolean))];
}

const EXCEPTIONS = [];
for (const field of recordFields) {
  const candidates = candidatesFor(field);
  const hit = candidates.find((c) => backendFields.has(c));
  if (hit) {
    const source = hit === mapperPairs.get(field) ? "mapper" : hit === camelToSnake(field) ? "camelCase" : "别名";
    pass(`StockRecord.${field} → ${hit}（${source}）`);
    continue;
  }
  const reason = ALLOWED_MISSING.get(field);
  if (reason !== undefined) {
    EXCEPTIONS.push({ field, reason, lookedFor: candidates });
    pass(`StockRecord.${field} 例外豁免（${reason}）`);
    continue;
  }
  fail(
    `StockRecord.${field} has no backend field (looked for ${candidates.join(" / ")})` +
      ` — 前端不许声明后端不存在的字段（见 ${PLAN_REF}）`
  );
}

// 单向断言的显式说明（防后来者"顺手"把反向断言加上）：后端多出来的字段不报错。
const ignoredByFrontend = [...backendFields].filter((b) => !recordFields.some((f) => candidatesFor(f).includes(b)));
pass(
  `单向断言：后端 ${backendFields.size} 个字段里有 ${ignoredByFrontend.length} 个前端不声明（合法忽略，不报错，勿加反向断言）`
);

if (failed) {
  console.log(`\n字段契约门禁未通过：${failed} 项`);
  process.exit(1);
}
console.log(
  `\n✔ 前端字段 ⊆ 后端 schema 契约通过：StockRecord 全部 ${recordFields.length} 个字段都能在 ` +
    `StockOut/StockEnrichedOut（${backendFields.size} 个字段）里找到，例外 ${EXCEPTIONS.length} 项`
);
