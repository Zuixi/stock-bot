import { useState } from "react";
import {
  Button,
  DatePicker,
  Input,
  InputNumber,
  Radio,
  Select,
  Space,
  Typography,
  message,
} from "antd";
import { useMutation } from "@tanstack/react-query";
import type { Dayjs } from "dayjs";
import {
  createJournalEntry,
  type JournalBetType,
  type JournalEntry,
  type JournalEntryCreate,
  type JournalHorizon,
  type JournalInvalidationKind,
  type JournalKind,
  type JournalPositionSize,
} from "@/shared/api/journal";
import { KIND_META, KIND_ORDER } from "../kinds";
import { POSITION_SIZE_META, POSITION_SIZE_ORDER } from "../sizes";
import { PeerHistoryCard } from "./PeerHistoryCard";

const { Text } = Typography;

/**
 * 「记一笔」表单：**恰好 3 个必填**（spec §S9.1）。
 *
 * 1. `thesis` —— 判断依据，≤200 字（`maxLength` + 计数器，超不了也看不见"超了"）；
 * 2. `invalidation` —— 失效条件：跌破某价 / 涨破某价 / 只看日期，**三选一 + 数值或日期**；
 * 3. `horizon_days` —— 观察期限 7 / 30 / 90 天，三选一。
 *
 * **缺字段时按钮禁用**（不是提交后报错）：`missing` 是唯一判据，同时驱动按钮 `disabled`
 * 与"还缺：…"提示——两边各写一份逻辑必然出现"按钮能点但一点就报错"的裂缝。
 *
 * 刻意**不给默认值**：失效条件与期限都必须由用户主动选一次。默认 `30 天` 看起来友好，
 * 实际会让"用户根本没想过多久之后复核"这件事被一个默认值悄悄盖掉——而 S9 的全部价值
 * 就在"到期时被迫面对结果"，期限是用户自己定的契约。
 *
 * 选填项（不带默认值之外的风险）：
 * - `symbol`：留空 = 市场级判断（后端按沪深 300 对比，页面显示"市场"）；
 * - `kind`：归档标签（观察/买/卖/放弃/持有）：只用于统计分栏，不改变结果数字；
 *   唯一的口径差别是 `pass` 计入判断、不计入收益（spec §S9.7.6）；
 * - `bet_type`：赌的是什么（方向/跑赢同行/市场级，spec §S9.7.3），**默认「方向」**，
 *   决定基准怎么选；创建时写进快照，事后不可改。
 * - `position_size`：仓位四选一（只观察/轻仓/标准/重仓，spec §S9.7.1），**默认「只观察」**；
 *   只观察计入判断、不计入收益统计（与 `pass` 同一条口径）。
 * - `confidence`：事前信心 1–5（spec §S9.7.5），**刻意留空**——留空 = 不参与校准；
 *   用可清空的 `Select` 而不是单选组，是因为单选组点过就回不到"没填"。
 *
 * 「标的」旁有「我决定不动」一键（spec §S9.7.6）：点它只把 `kind` 设为 `pass`，
 * **不跳过 3 个必填**——放弃的判断同样要写清"当时看到了什么"和"什么情况说明想错了"，
 * 否则"没做"这件事没有可复盘的依据。
 *
 * 创建成功后**不可修改**（接口层无 PATCH/PUT/DELETE），所以这里只做"记一笔"，
 * 不提供编辑态——这不是漏做，是 S9 的 append-only 纪律。
 */

/** 归档标签（spec §S9.1 的五枚举；`pass` = 我决定不动，只计入判断、不计入收益）。 */
const KIND_OPTIONS: { value: JournalKind; label: string }[] = KIND_ORDER.map((value) => ({
  value,
  label: KIND_META[value].label,
}));

/**
 * 赌的是什么（spec §S9.7.3）：决定基准怎么选，**默认「方向」**。
 * 文案只描述基准口径，不评价结果（不出现判断词）。
 */
const BET_TYPE_OPTIONS: { value: JournalBetType; label: string }[] = [
  { value: "direction", label: "方向" },
  { value: "relative", label: "跑赢同行" },
  { value: "market", label: "市场级" },
];

/** 失效条件三形态（与后端 `invalidation.kind` 逐字一致）。 */
const INVALIDATION_OPTIONS: { value: JournalInvalidationKind; label: string }[] = [
  { value: "price_below", label: "跌破某价格" },
  { value: "price_above", label: "涨破某价格" },
  { value: "date_only", label: "只看日期（到期即算触发）" },
];

/**
 * 仓位四选一（spec §S9.7.1）：`none` = 只观察，是**默认**（多数记录只是"想了一下"）。
 *
 * 默认值只在这里出现一次：列表行与统计卡**不**用默认值补齐缺失的 `position_size`
 * （那会把"后端没给"显示成"用户选了只观察"）。
 */
const POSITION_SIZE_OPTIONS: { value: JournalPositionSize; label: string }[] = POSITION_SIZE_ORDER.map(
  (value) => ({ value, label: POSITION_SIZE_META[value].label })
);

/**
 * 事前信心 1–5（spec §S9.7.5 的固定映射 20% / 35% / 50% / 65% / 80%）。
 *
 * 用可清空的 `Select` 而不是 `Radio.Group`：`Radio.Group` 一旦点过就**没法回到"没填"**，
 * 那会把"我不确定"悄悄变成某一档；而"留空 = 不参与校准"正是 spec 允许的常态。
 */
const CONFIDENCE_OPTIONS: { value: number; label: string }[] = [1, 2, 3, 4, 5].map((value) => ({
  value,
  label: `${value} 分`,
}));

const HORIZONS: readonly JournalHorizon[] = [7, 30, 90];

/** 提示里用的**字段人话名**（顺序即表单顺序；缺几项就写几项）。 */
const MISSING_LABEL = {
  thesis: "判断依据",
  invalidation: "失效条件",
  horizon: "观察期限",
} as const;

interface EntryFormProps {
  /** 创建成功后的回调（面板据此刷新三份列表与统计）。 */
  onCreated: () => void;
}

export function EntryForm({ onCreated }: EntryFormProps) {
  const [thesis, setThesis] = useState("");
  const [symbol, setSymbol] = useState("");
  const [kind, setKind] = useState<JournalKind>("watch");
  const [betType, setBetType] = useState<JournalBetType>("direction");
  const [invKind, setInvKind] = useState<JournalInvalidationKind | undefined>(undefined);
  const [priceValue, setPriceValue] = useState<number | null>(null);
  const [dateValue, setDateValue] = useState<Dayjs | null>(null);
  const [horizon, setHorizon] = useState<JournalHorizon | undefined>(undefined);
  // 仓位默认「只观察」（spec §S9.7.1 的默认值）；信心**刻意留空**（见 `CONFIDENCE_OPTIONS`）。
  const [positionSize, setPositionSize] = useState<JournalPositionSize>("none");
  const [confidence, setConfidence] = useState<number | undefined>(undefined);
  // 刚创建的那一条（spec §S9.6.5：创建成功后**立刻**把"同类情形"还给用户）。
  // 不下发到列表面板，是因为这段只在"刚写完"这一刻有意义，刷新后由快照里那份承载。
  const [created, setCreated] = useState<JournalEntry | null>(null);

  const mutation = useMutation({
    mutationFn: (payload: JournalEntryCreate) => createJournalEntry(payload),
    onSuccess: (entry) => {
      message.success("已记下这一笔");
      setCreated(entry);
      // 后端把状态分好，前端不自己往列表里塞条目：由面板统一失效重取三份列表 + 统计，
      // 这样"创建后立刻在列表里看到"与"到期/复盘后的移动"走的是同一条数据路径。
      onCreated();
      setThesis("");
      setInvKind(undefined);
      setPriceValue(null);
      setDateValue(null);
      setHorizon(undefined);
      // 仓位/信心回到默认（仓位「只观察」、信心留空）：下一笔重新选，不把上一笔的选择带过去。
      setPositionSize("none");
      setConfidence(undefined);
    },
    onError: () => {
      // 不把后端 message 直接上屏（可能是英文/机器串）；用户要的是"没记上，再试一次"。
      message.error("这一笔没能记下，请稍后重试");
    },
  });

  const thesisOk = thesis.trim().length > 0 && thesis.length <= 200;
  const invValueOk =
    invKind === "date_only"
      ? dateValue !== null
      : (priceValue !== null && priceValue > 0);
  const invOk = invKind !== undefined && invValueOk;
  const horizonOk = horizon !== undefined;

  // 缺项清单：单一判据，同时驱动提示文案与按钮禁用（见组件头注释）。
  const missing: string[] = [];
  if (!thesisOk) missing.push(MISSING_LABEL.thesis);
  if (!invOk) missing.push(MISSING_LABEL.invalidation);
  if (!horizonOk) missing.push(MISSING_LABEL.horizon);

  const canSubmit = missing.length === 0 && !mutation.isPending;

  const buildPayload = (): JournalEntryCreate | null => {
    if (!canSubmit || invKind === undefined || horizon === undefined) return null;
    const invalidation =
      invKind === "date_only"
        ? { kind: invKind, value: dateValue ? dateValue.format("YYYY-MM-DD") : "" }
        : { kind: invKind, value: priceValue ?? 0 };
    // 防御：`canSubmit` 已保证值都在，但 `?? 0`/空串一旦漏过会写出脏数据，故再兜一次。
    if (invalidation.value === 0 || invalidation.value === "") return null;
    return {
      symbol: symbol.trim() ? symbol.trim() : undefined,
      kind,
      bet_type: betType,
      // 仓位总是随载荷发（默认 `none`）；信心留空时**不发这个键**（= 不参与校准，
      // 与 `confidence: 0` / 缺省成一档 3 是两件事）。
      position_size: positionSize,
      confidence,
      thesis: thesis.trim(),
      invalidation,
      horizon_days: horizon,
    };
  };

  const submit = () => {
    const payload = buildPayload();
    if (payload) mutation.mutate(payload);
  };

  return (
    <form
      className="journal-form"
      data-testid="journal-form"
      onSubmit={(event) => {
        event.preventDefault();
        submit();
      }}
    >
      <div className="journal-form__row">
        <Text className="journal-form__label">判断依据（必填）</Text>
        <Input.TextArea
          className="journal-form__thesis"
          data-testid="journal-thesis"
          value={thesis}
          onChange={(event) => setThesis(event.target.value)}
          placeholder="写下你此刻的判断：看到了什么、为什么这么看"
          maxLength={200}
          showCount
          autoSize={{ minRows: 3, maxRows: 6 }}
        />
      </div>

      <div className="journal-form__row">
        <Text className="journal-form__label">失效条件（必填）</Text>
        <Space wrap size={8} align="start">
          <Select
            className="journal-form__inv-kind"
            data-testid="journal-inv-kind"
            value={invKind}
            onChange={(value: JournalInvalidationKind) => setInvKind(value)}
            options={INVALIDATION_OPTIONS}
            placeholder="选择失效条件"
            aria-label="失效条件"
          />
          {(invKind === "price_below" || invKind === "price_above") && (
            <InputNumber
              className="journal-form__inv-price"
              data-testid="journal-inv-price"
              value={priceValue}
              onChange={(value) => setPriceValue(value)}
              placeholder={invKind === "price_below" ? "跌破价" : "涨破价"}
              aria-label="价格阈值"
              min={0}
              precision={2}
              addonAfter="元"
            />
          )}
          {invKind === "date_only" && (
            <DatePicker
              className="journal-form__inv-date"
              data-testid="journal-inv-date"
              value={dateValue}
              onChange={(value) => setDateValue(value)}
              placeholder="失效日期"
              aria-label="失效日期"
            />
          )}
        </Space>
        <Text className="journal-form__hint">
          写下"什么情况出现就说明这次判断错了"，到期时系统会用它判定是否触发。
        </Text>
      </div>

      <div className="journal-form__row">
        <Text className="journal-form__label">观察期限（必填）</Text>
        <Radio.Group
          data-testid="journal-horizon"
          value={horizon}
          onChange={(event) => setHorizon(event.target.value as JournalHorizon)}
        >
          {HORIZONS.map((days) => (
            <Radio.Button key={days} value={days}>
              {`${days} 天`}
            </Radio.Button>
          ))}
        </Radio.Group>
        <Text className="journal-form__hint">到期那天的记录会出现在「待复盘」里。</Text>
      </div>

      <div className="journal-form__row">
        <Text className="journal-form__label">赌的是什么（选填，默认「方向」；决定用哪个基准）</Text>
        <Radio.Group
          data-testid="journal-bet-type"
          value={betType}
          onChange={(event) => setBetType(event.target.value as JournalBetType)}
        >
          {BET_TYPE_OPTIONS.map((option) => (
            <Radio.Button key={option.value} value={option.value}>
              {option.label}
            </Radio.Button>
          ))}
        </Radio.Group>
        <Text className="journal-form__hint">
          方向 → 沪深300（赌这只/这个行业会涨）；跑赢同行 → 所属行业成分股等权（赌它比同行强）；市场级 → 沪深300。
          创建时选定，之后不可改。
        </Text>
      </div>

      <div className="journal-form__row">
        <Text className="journal-form__label">仓位（选填，默认「只观察」）</Text>
        <Radio.Group
          data-testid="journal-position-size"
          value={positionSize}
          onChange={(event) => setPositionSize(event.target.value as JournalPositionSize)}
        >
          {POSITION_SIZE_OPTIONS.map((option) => (
            <Radio.Button key={option.value} value={option.value}>
              {option.label}
            </Radio.Button>
          ))}
        </Radio.Group>
        <Text className="journal-form__hint">
          只观察 = 只记判断、不下单（不计入收益统计）；其余三档按粗档加权。
        </Text>
      </div>

      <div className="journal-form__row">
        <Text className="journal-form__label">
          你当时有多大把握（选填，1–5；留空 = 不参与校准）
        </Text>
        <Select
          className="journal-form__confidence"
          data-testid="journal-confidence"
          value={confidence}
          onChange={(value) => setConfidence(value ?? undefined)}
          options={CONFIDENCE_OPTIONS}
          allowClear
          placeholder="留空 = 不参与校准"
          aria-label="你当时有多大把握"
        />
        <Text className="journal-form__hint">
          1 = 最没把握，5 = 最有把握；只用于「你的信心与实际结果的对照」，不用于评价你。
        </Text>
      </div>

      <div className="journal-form__row">
        <Text className="journal-form__label">标的（选填，留空 = 市场级判断）</Text>
        <Space wrap size={8}>
          <Input
            className="journal-form__symbol"
            data-testid="journal-symbol"
            value={symbol}
            onChange={(event) => setSymbol(event.target.value)}
            placeholder="例如 600519"
            aria-label="标的代码"
            maxLength={16}
          />
          {/* 一键把归档标签设为 `pass`；**不碰** 3 个必填，放弃的判断也要能复盘 */}
          <Button data-testid="journal-pass" onClick={() => setKind("pass")}>
            我决定不动
          </Button>
        </Space>
        {kind === "pass" ? (
          <Text className="journal-form__pass-hint" data-testid="journal-pass-hint">
            已标为「我决定不动」：只观察、不计收益（这条计入判断统计，不计入任何收益均值）。
          </Text>
        ) : (
          <Text className="journal-form__hint">
            放弃的判断同样值得记一笔：点「我决定不动」，3 个必填照旧。
          </Text>
        )}
      </div>

      <div className="journal-form__row">
        <Text className="journal-form__label">归档标签（选填，用于统计分栏）</Text>
        <Select
          className="journal-form__kind"
          data-testid="journal-kind"
          value={kind}
          onChange={(value: JournalKind) => setKind(value)}
          options={KIND_OPTIONS}
          aria-label="归档标签"
        />
      </div>

      <div className="journal-form__foot">
        {missing.length > 0 ? (
          <Text className="journal-form__missing" data-testid="journal-missing">
            {`还缺：${missing.join("、")}`}
          </Text>
        ) : (
          <Text className="journal-form__hint">写下的这三行创建后不可修改。</Text>
        )}
        <Button
          type="primary"
          htmlType="submit"
          data-testid="journal-submit"
          disabled={!canSubmit}
          loading={mutation.isPending}
        >
          记下这一笔
        </Button>
      </div>

      {/* 同类历史（spec §S9.6.5）：只在刚创建成功时展示；缺字段时组件自己整卡不渲染 */}
      {created && <PeerHistoryCard entry={created} />}
    </form>
  );
}
