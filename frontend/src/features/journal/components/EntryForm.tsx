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
  type JournalEntryCreate,
  type JournalHorizon,
  type JournalInvalidationKind,
  type JournalKind,
} from "@/shared/api/journal";

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
 * 选填两项（不带默认值之外的风险）：
 * - `symbol`：留空 = 市场级判断（后端按沪深 300 对比，页面显示"市场"）；
 * - `kind`：归档标签（观察/买/卖/放弃/持有），**不参与任何计算**。
 *
 * 创建成功后**不可修改**（接口层无 PATCH/PUT/DELETE），所以这里只做"记一笔"，
 * 不提供编辑态——这不是漏做，是 S9 的 append-only 纪律。
 */

/** 归档标签（spec §S9.1 的五枚举；只用于用户自己分类）。 */
const KIND_OPTIONS: { value: JournalKind; label: string }[] = [
  { value: "watch", label: "观察" },
  { value: "buy", label: "买" },
  { value: "sell", label: "卖" },
  { value: "pass", label: "放弃" },
  { value: "hold", label: "持有" },
];

/** 失效条件三形态（与后端 `invalidation.kind` 逐字一致）。 */
const INVALIDATION_OPTIONS: { value: JournalInvalidationKind; label: string }[] = [
  { value: "price_below", label: "跌破某价格" },
  { value: "price_above", label: "涨破某价格" },
  { value: "date_only", label: "只看日期（到期即算触发）" },
];

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
  const [invKind, setInvKind] = useState<JournalInvalidationKind | undefined>(undefined);
  const [priceValue, setPriceValue] = useState<number | null>(null);
  const [dateValue, setDateValue] = useState<Dayjs | null>(null);
  const [horizon, setHorizon] = useState<JournalHorizon | undefined>(undefined);

  const mutation = useMutation({
    mutationFn: (payload: JournalEntryCreate) => createJournalEntry(payload),
    onSuccess: () => {
      message.success("已记下这一笔");
      // 后端把状态分好，前端不自己往列表里塞条目：由面板统一失效重取三份列表 + 统计，
      // 这样"创建后立刻在列表里看到"与"到期/复盘后的移动"走的是同一条数据路径。
      onCreated();
      setThesis("");
      setInvKind(undefined);
      setPriceValue(null);
      setDateValue(null);
      setHorizon(undefined);
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
        <Text className="journal-form__label">标的（选填，留空 = 市场级判断）</Text>
        <Input
          className="journal-form__symbol"
          data-testid="journal-symbol"
          value={symbol}
          onChange={(event) => setSymbol(event.target.value)}
          placeholder="例如 600519"
          aria-label="标的代码"
          maxLength={16}
        />
      </div>

      <div className="journal-form__row">
        <Text className="journal-form__label">归档标签（选填，只用于你自己分类）</Text>
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
    </form>
  );
}
