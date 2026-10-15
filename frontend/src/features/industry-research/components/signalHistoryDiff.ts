/**
 * 信号历史 **diff 化**（Phase 4 §5.5）——纯函数，无 React、无 DOM。
 *
 * ## 为什么要有这个文件
 *
 * `SignalPanel` 的每条历史信号原先都整段渲染 `reason`（=`触发依据`）。而规则引擎产出的
 * `reason` 是**规则全集**（"猪粮比处于深度亏损区间；能繁存栏连续回落；行业连续亏损"），
 * 相邻几天往往只差一个数——于是同一句话在一屏里重复 6 遍，用户要逐字找差异。
 *
 * 本模块把"每条都抄一遍全集"改成"**首条给全集，后续只给变化项**；连续无变化折叠成
 * `连续 N 条无变化`"。**不删任何一条信号**（日期与信号类型照旧逐条渲染），删掉的只是重复文本。
 *
 * ## 与后端 `reason` 的约定
 *
 * 分隔符是**全角分号 `；`**——后端 `industry_metric_service.py:800` 的
 * `"reason": "；".join(out.reasons)` 就是这条契约（`_signal_out` 直出 `row.reason`，
 * 前端不做任何改写）。故这里也只按 `；` 切，**不**自作聪明地兼容半角/其它分隔符：
 * 切分口径与产地不一致，会造出"看起来变了实际没变"的假 diff。
 *
 * ## 对齐规则（按优先级）
 *
 * 1. **完全相同** → 该段是"没变"，丢弃；并把它从候选池移除，避免后续段重复认领同一条基线。
 * 2. **去掉数字后文本相同**（`猪粮比 4.65` vs `猪粮比 4.34`）→ 同一个指标换了个值，
 *    渲染成 `猪粮比 4.65 → 4.34`（**这才是 diff 的价值所在**：一眼看到哪个数动了）。
 * 3. **前缀命中**（`能繁环比第 3 个月回落` vs `能繁环比第 2 个月回落`：去数字后并不同，
 *    因为 `个月` 被 `\D+` 吸进了同一段）→ 取最长公共前缀，若前缀长度 ≥ 2 且以非数字收尾，
 *    认定为同一指标，渲染 `老 → 新`。这条比第 2 条更松，故排在后面，只在第 2 条落空时才试。
 * 4. 都不命中 → 该段是**新出现的**变化项，原样渲染。
 *
 * 若整条信号的段全部落在 1（即与基线一字不差）→ 记为**无变化**，由调用方折叠。
 */

/** 一段触发依据经 diff 后的形态 */
export interface SignalChange {
  /** 变化类型：`changed` = 新出现或换了值；`value` = 同一指标换了数字（渲染 `老 → 新`） */
  kind: "changed" | "value";
  /** 当前段原文（`changed`）或当前值（`value` 的右侧） */
  text: string;
  /** 仅 `kind === "value"`：基线里的旧值 */
  from?: string;
}

export interface SignalDiffEntry {
  /** 信号日期（`Signal.effectiveDate`） */
  effectiveDate: string;
  /** 是否为首条（首条给完整 `触发依据`） */
  isFirst: boolean;
  /** 首条的完整段列表；非首条为空数组 */
  full: string[];
  /** 非首条：只含变化项（可能为空 = 与上一条无变化） */
  changes: SignalChange[];
}

/**
 * 一段文本里的**全部数字**（含小数、可选正负号）。无数字返回空数组。
 *
 * ⚠️ **必须每次现造正则**，不能用模块级 `const NUMS_RE = /…/g`：带 `g` 的正则对象
 * 会记住 `lastIndex`，同一个对象被 `match()` 连续调用时，第二次从上次的位置继续扫
 * —— `"换手率 4"` 第一次匹配后 `lastIndex=5`，第二次对 `"换手率 4"` 再 `match` 就
 * 只从下标 5 往后扫、**永不命中**，于是"骨架相同"的判断恒为 false（实测：
 * `check-signal-diff.mjs` 的反向陷阱用例因此转红，真因是这里而不是骨架逻辑）。
 */
function numbersOf(text: string): string[] {
  return text.match(/-?\d+(?:\.\d+)?/g) ?? [];
}

/**
 * 去掉全部数字后的"骨架"（用作同一指标的身份键）。
 *
 * **已知边界（实测，不假装它完美）**：数字位一律替换成 `#` 且**不保留位数**，故
 * `换手率 4` 与 `换手率 40` 的骨架都是 `换手率 #` ⇒ 会被读成"同一个数变了"。
 * 保留位数又会把 `4.65` 与 `4.7` 判成不同骨架，反而丢掉真实 diff。这里选**位数无关**，因为：
 * ① 真实 `reason` 是固定模板（`猪粮比 4.25 处于一级预警区间`、`生猪均价 10.07 元/kg`），
 * 段内文字足以区分指标；② 误判的代价只是多显示一次 `老 → 新`，**两端原文都完整保留**，
 * 不会输出错误数字；③ 反过来（漏判成"没变"）才会让用户看不到真实的数值变化。
 * 若将来出现"同一数字位、不同指标"的真实样本，再引入按指标名对齐的注册表。
 * 前缀规则（`commonPrefix`）只在**骨架不同**时兜底，不受此边界影响。
 */
function skeletonOf(text: string): string {
  return text.replace(/-?\d+(?:\.\d+)?/g, "#").trim();
}

/**
 * 最长公共前缀的长度**与是否以非数字结尾**。
 * `能繁环比第 3 个月回落` / `能繁环比第 2 个月回落` 的公共前缀是 `能繁环比第 ` ——
 * 以空格结尾（非数字）⇒ 可判定为同一指标；`猪粮比 4` / `猪粮比 40` 的公共前缀以数字 `4`
 * 结尾 ⇒ **不得**判定为同一指标（否则 4→40 会被读成"同一个数变大了"，而它可能是两个指标）。
 */
function commonPrefix(text: string, other: string): { len: number; endsNonDigit: boolean } {
  const n = Math.min(text.length, other.length);
  let i = 0;
  while (i < n && text[i] === other[i]) i += 1;
  const last = i > 0 ? text[i - 1] : "";
  return { len: i, endsNonDigit: last !== "" && !/\d/.test(last) };
}

/**
 * 把一条新信号与它的基线（时间上的**下一条更早**的信号）对齐，产出变化项。
 *
 * 复用池语义：基线里的每一段最多被认领一次。否则两条都写着同一句话时，第二条会
 * "再次认领"已被第一条吃掉的那段，结果是两条都被判成"没变"——反向的错误。
 */
export function diffReason(baseline: string | null, current: string | null): SignalChange[] {
  const curSegs = splitReason(current);
  if (curSegs.length === 0) return [];
  const baseSegs = splitReason(baseline);
  if (baseSegs.length === 0) {
    // 基线没有可对齐的内容（首条、或上游 `reason` 为空）⇒ 全部视为变化项，不做假 diff
    return curSegs.map((text) => ({ kind: "changed" as const, text }));
  }

  const taken = new Array<boolean>(baseSegs.length).fill(false);
  const out: SignalChange[] = [];

  for (const seg of curSegs) {
    // 1) 完全相同 → 没变
    const exact = baseSegs.findIndex((b, i) => !taken[i] && b === seg);
    if (exact >= 0) {
      taken[exact] = true;
      continue;
    }

    // 2) 同一骨架 + 数字不同 → `老 → 新`
    const sk = skeletonOf(seg);
    const bySkeleton = baseSegs.findIndex(
      (b, i) => !taken[i] && numbersOf(b).length > 0 && skeletonOf(b) === sk,
    );
    if (bySkeleton >= 0) {
      taken[bySkeleton] = true;
      out.push({ kind: "value", text: seg, from: baseSegs[bySkeleton] });
      continue;
    }

    // 3) 前缀命中（比 2 更松，故排在后面）
    let byPrefix = -1;
    let prefixLen = 0;
    for (let i = 0; i < baseSegs.length; i += 1) {
      if (taken[i]) continue;
      const b = baseSegs[i];
      if (numbersOf(b).length === 0 || numbersOf(seg).length === 0) continue;
      const { len, endsNonDigit } = commonPrefix(seg, b);
      if (len >= 2 && endsNonDigit && len > prefixLen) {
        byPrefix = i;
        prefixLen = len;
      }
    }
    if (byPrefix >= 0) {
      taken[byPrefix] = true;
      out.push({ kind: "value", text: seg, from: baseSegs[byPrefix] });
      continue;
    }

    // 4) 新出现的段
    out.push({ kind: "changed", text: seg });
  }

  return out;
}

/** 按后端约定的**全角分号**切段，并去掉空段（`"a；；b"` → `["a","b"]`） */
export function splitReason(reason: string | null | undefined): string[] {
  if (!reason) return [];
  return reason
    .split("；")
    .map((s) => s.trim())
    .filter((s) => s.length > 0);
}

/** 折叠后的历史条目：`kind === "collapsed"` 时是一条"连续 N 条无变化"的汇总行 */
export interface SignalHistoryRow {
  kind: "signal" | "collapsed";
  /** 折叠行展示的日期区间（首尾）；单条信号行是它自己的日期 */
  effectiveDate: string;
  /** 该行代表的**信号条数**：普通行恒为 1，折叠行为 run 长度（≥2）。**没有任何一条被丢掉**。 */
  entries: number;
  /** 折叠行：区间末端的日期；`signal` 行为空串 */
  untilDate: string;
  /** 本行要渲染的变化项：普通行 = 该条的变化；折叠行恒为空（run 内每条都无变化） */
  changes: SignalChange[];
  /** `true` = 与基线相比**一条变化都没有**（渲染「与上一条无变化」或折叠行） */
  unchanged: boolean;
  /** 该行信号自身的类型（折叠行取区间内最新一条的类型） */
  signalType: string;
}

/**
 * 把 `history`（**按时间倒序**，即 `history[0]` 最新，与后端 payload 同序）压成待渲染行。
 *
 * - `history[0]` 是**首条**：给完整 `触发依据`（`isFirst`）。
 * - 自 `history[1]` 起逐条与**时间上更晚的那条**（`history[i-1]`）比，只留变化项。
 * - 连续 ≥2 条"无变化"折叠成一行 `连续 N 条无变化`（`collapsedCount = N`）。
 *
 * 顺序与条数**不变**：`expand` 后的信号条数与输入的非折叠条目一一对应，只是文本变短。
 */
export function buildSignalHistoryRows(history: SignalLike[]): SignalHistoryRow[] {
  const rows: SignalHistoryRow[] = [];
  if (history.length === 0) return rows;

  const first = history[0];
  rows.push({
    kind: "signal",
    effectiveDate: first.effectiveDate,
    entries: 1,
    untilDate: "",
    changes: [],
    unchanged: false,
    signalType: first.signalType,
  });

  // 从 1 开始：逐条与更晚的邻居比（`baseline` = 上一条的 reason）
  const perEntry: Array<{ sig: SignalLike; changes: SignalChange[] }> = [];
  for (let i = 1; i < history.length; i += 1) {
    const changes = diffReason(history[i - 1].reason, history[i].reason);
    perEntry.push({ sig: history[i], changes });
  }

  let i = 0;
  while (i < perEntry.length) {
    const { changes } = perEntry[i];
    if (changes.length > 0) {
      rows.push({
        kind: "signal",
        effectiveDate: perEntry[i].sig.effectiveDate,
        entries: 1,
        untilDate: "",
        changes,
        unchanged: false,
        signalType: perEntry[i].sig.signalType,
      });
      i += 1;
      continue;
    }
    // 收集连续的"无变化" run
    let j = i;
    while (j < perEntry.length && perEntry[j].changes.length === 0) j += 1;
    const run = j - i;
    if (run === 1) {
      // 只有一条无变化：不值得折叠成汇总行，就地渲染成一条"与上一条无变化"
      rows.push({
        kind: "signal",
        effectiveDate: perEntry[i].sig.effectiveDate,
        entries: 1,
        untilDate: "",
        changes: [],
        unchanged: true,
        signalType: perEntry[i].sig.signalType,
      });
    } else {
      rows.push({
        kind: "collapsed",
        effectiveDate: perEntry[i].sig.effectiveDate,
        entries: run,
        untilDate: perEntry[j - 1].sig.effectiveDate,
        changes: [],
        unchanged: true,
        signalType: perEntry[j - 1].sig.signalType,
      });
    }
    i = j;
  }

  return rows;
}

/** 本模块只依赖这四个字段；用结构化类型而不是 import `Signal`，便于纯函数单测不拉 API 类型 */
export interface SignalLike {
  signalType: string;
  effectiveDate: string;
  reason: string | null;
}
