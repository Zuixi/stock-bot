import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import {
  Alert,
  Breadcrumb,
  Button,
  Checkbox,
  DatePicker,
  Empty,
  Input,
  Segmented,
  Select,
  Space,
  Spin,
  Table,
  Tag,
  Tooltip,
  Typography,
} from "antd";
import { CopyOutlined, ReloadOutlined, SearchOutlined } from "@ant-design/icons";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import dayjs from "dayjs";
import type { ColumnsType } from "antd/es/table";
import { ChangeText, NumberText, SectionCard, StateWrapper, formatAsOfDate, formatCnDate, qualityBadgeText } from "@/shared/ui";
import {
  fetchRegulatoryRadar,
  type RegulatoryItem,
  type RegulatoryRadar,
} from "@/shared/api/regulatory";
import "./regulatory.css";

const { Text, Paragraph } = Typography;

/**
 * `/market/regulatory` —— 监管异动雷达（2026-10-20）。
 *
 * **只陈述可复算的事实**：按交易所《交易规则》的「连续 3 个交易日收盘价涨跌幅偏离值
 * 累计」口径，算出今日已达线 / 临近 / 明日再涨多少触发，并列出近 10 个交易日的触发日。
 * 不预测、不推荐、不构成投资建议。
 *
 * 口径纪律（与后端 `regulatory_anomaly.py` 的 docstring 同源）：
 * - 阈值、基准指数、失效场景**全部来自 payload**（`thresholds` / `caveats`），
 *   前端不复制一份会漂移的常量表；
 * - `dev3Pct === null` 是「窗口内有停牌/上市不足 3 日」，渲染 `--` 并给 tooltip，
 *   绝不 `?? 0`（0 会被读成「没有偏离」）；
 * - 已达线的票不给「明日触发价」：最近两日偏离已越过阈值时，明日持平也会触发，
 *   再给一个价格是误导（后端已置 `nextTrigger=null`）。
 */

const DEGRADED_TEXT: Record<string, string> = {
  no_quotes: "库里还没有行情数据，无法计算监管异动。",
  not_a_trade_day: "所选日期没有行情（非交易日或数据尚未入库）。",
  price_limits_missing:
    "当日缺少交易所涨跌停价：涨停/跌停股可能漏列，明日触发价按板块固定限幅估算。",
  // 基准指数日线缺日：个股行情在，但 3 日偏离算不出。此时 KPI 全 0 与“今天没有异动”
  // 长得一模一样，必须明说，否则读者会得到相反结论。
  benchmark_missing:
    "基准指数日线缺日：3 日偏离值算不出来，下面各行的状态与 KPI 不可当“没有异动”读。",
};

const QUALITY_TEXT: Record<string, string> = {
  // 共享三态**不在这里写文案**：与卡片徽标同源于 `FreshnessNote.qualityBadgeText`，
  // 它注释里声明自己是全站单一渲染点。之前这里各写一份（收盘口径 / 按最近完整交易日），
  // 同一屏两处措辞不一致——已改成真同源。
  complete: qualityBadgeText("complete") ?? "收盘",
  partial: qualityBadgeText("partial") ?? "未完整",
  fallback: qualityBadgeText("fallback") ?? "按最近完整日",
  // `requested` 是本页特有的语义（?date= 指定了非缺省日），共享层没有这个词
  requested: "指定日期",
};

const EXCHANGE_TEXT: Record<string, string> = {
  Shanghai_Stocks: "沪市",
  Shenzen_Stocks: "深市",
  Beijing_Stocks: "北交所",
};

function statusTag(item: RegulatoryItem) {
  const dirSuffix = item.direction === "down" ? "（跌）" : "";
  if (item.status === "triggered") {
    return (
      <Tag color={item.direction === "up" ? "red" : "green"} className="reg-status">
        已达线{dirSuffix}
      </Tag>
    );
  }
  if (item.status === "near") {
    return (
      <Tag color="orange" className="reg-status">
        临近{dirSuffix}
      </Tag>
    );
  }
  return (
    <Tag className="reg-status">观察{dirSuffix}</Tag>
  );
}

function ladderText(item: RegulatoryItem): string {
  if (item.streak != null && item.streak > 0) {
    return `${item.streak} 连板`;
  }
  if (item.boardsInWindow != null && item.boardsInWindow >= 2) {
    const span = item.daysSpan != null ? `${item.daysSpan} 天 ` : "";
    return `${span}${item.boardsInWindow} 板`;
  }
  if (item.limitUp) return "首板";
  if (item.limitDown) return "跌停";
  return "--";
}

/** 3 日偏离 / 阈值：`null` 是「窗口内停牌」，不是 0。 */
function Dev3Cell({ item }: { item: RegulatoryItem }) {
  if (item.dev3Pct == null) {
    return (
      <Tooltip title="3 日窗口不完整：个股停牌 / 上市不足 3 个交易日 / 基准指数缺日">
        <span className="reg-muted">--（窗口缺行）</span>
      </Tooltip>
    );
  }
  const reached = Math.abs(item.dev3Pct) >= item.thresholdPct;
  return (
    <span className="reg-dev">
      <b className={reached ? "reg-dev--hit" : undefined}>{item.dev3Pct.toFixed(2)}%</b>
      <span className="reg-muted"> / {item.thresholdPct.toFixed(0)}%</span>
    </span>
  );
}

function TriggerCell({ item }: { item: RegulatoryItem }) {
  const t = item.nextTrigger;
  if (item.status === "triggered") {
    return <span className="reg-muted">已触发</span>;
  }
  if (!t) return <span className="reg-muted">--</span>;
  // 「持平即触发」的方向判据不同：涨向 needed<=0、跌向 needed>=0 都表示明日不动也越线
  const alreadyMet = item.direction === "up" ? t.neededPct <= 0 : t.neededPct >= 0;
  if (alreadyMet) {
    return <span className="reg-trigger--hit">持平即触发</span>;
  }
  if (!t.feasible) {
    return (
      <Tooltip title={`明日涨跌幅上限约 ${t.limitPct.toFixed(1)}%，所需幅度超过上限`}>
        <span className="reg-muted">涨跌停也难触发</span>
      </Tooltip>
    );
  }
  const up = item.direction === "up";
  return (
    <span className="reg-trigger">
      <b>
        {up ? "≥" : "≤"} {t.price.toFixed(2)}
      </b>
      <span className="reg-muted">
        {" "}
        需 {up ? "+" : "-"}
        {Math.abs(t.neededPct).toFixed(2)}%
      </span>
    </span>
  );
}

function itemColumns(): ColumnsType<RegulatoryItem> {
  return [
    {
      title: "状态",
      key: "status",
      width: 96,
      render: (_, item) => statusTag(item),
    },
    {
      title: "股票",
      key: "name",
      width: 190,
      render: (_, item) => (
        <Space direction="vertical" size={0}>
          <Link to={`/company/${item.symbol}`} className="reg-name">
            {item.name}
          </Link>
          <span className="reg-muted reg-code">
            {item.symbol} · {item.board}
            {item.isSt ? " · ST" : ""}
          </span>
        </Space>
      ),
    },
    {
      title: "板数",
      key: "ladder",
      width: 96,
      sorter: (a, b) =>
        (a.streak ?? a.boardsInWindow ?? -1) - (b.streak ?? b.boardsInWindow ?? -1),
      render: (_, item) => <span className="reg-ladder">{ladderText(item)}</span>,
    },
    {
      title: "收盘",
      dataIndex: "close",
      key: "close",
      width: 84,
      align: "right",
      sorter: (a, b) => a.close - b.close,
      render: (value: number) => <NumberText value={value} />,
    },
    {
      title: "今日",
      key: "day",
      width: 84,
      align: "right",
      sorter: (a, b) => (a.dayPct ?? Number.NEGATIVE_INFINITY) - (b.dayPct ?? Number.NEGATIVE_INFINITY),
      render: (_, item) => <ChangeText value={item.dayPct} />,
    },
    {
      title: "3 日偏离 / 阈值",
      key: "dev3",
      width: 150,
      sorter: (a, b) =>
        (a.dev3Pct ?? Number.NEGATIVE_INFINITY) - (b.dev3Pct ?? Number.NEGATIVE_INFINITY),
      render: (_, item) => <Dev3Cell item={item} />,
    },
    {
      title: "10 日异动",
      dataIndex: "events10d",
      key: "events",
      width: 92,
      align: "right",
      sorter: (a, b) => a.events10d - b.events10d,
      render: (n: number) => <span className="num">{n} 次</span>,
    },
    {
      title: "严重异常",
      key: "severe",
      width: 160,
      render: (_, item) =>
        item.severeFlags.length > 0 ? (
          <Space size={4} wrap>
            {item.severeFlags.map((flag) => (
              <Tag key={flag} color="volcano">
                {flag}
              </Tag>
            ))}
          </Space>
        ) : (
          <span className="reg-muted">--</span>
        ),
    },
    {
      title: "明日触发",
      key: "trigger",
      width: 150,
      sorter: (a, b) =>
        (a.nextTrigger?.neededPct ?? Number.POSITIVE_INFINITY) -
        (b.nextTrigger?.neededPct ?? Number.POSITIVE_INFINITY),
      render: (_, item) => <TriggerCell item={item} />,
    },
    {
      title: "基准",
      key: "benchmark",
      width: 88,
      render: (_, item) => (
        <Tooltip title={`${item.benchmarkCode} · 阈值 ${item.thresholdPct.toFixed(0)}%`}>
          <span className="reg-muted">{item.benchmarkName}</span>
        </Tooltip>
      ),
    },
  ];
}

function Kpi({ label, value, tone }: { label: string; value: number; tone?: "up" | "down" | "warn" }) {
  return (
    <div className={`reg-kpi${tone ? ` reg-kpi--${tone}` : ""}`}>
      <div className="reg-kpi__value num">{value}</div>
      <div className="reg-kpi__label">{label}</div>
    </div>
  );
}

function PreviewTable({ items }: { items: RegulatoryItem[] }) {
  const columns: ColumnsType<RegulatoryItem> = [
    {
      title: "股票",
      key: "name",
      render: (_, item) => (
        <Space size={6}>
          <Link to={`/company/${item.symbol}`}>{item.name}</Link>
          <span className="reg-muted reg-code">{item.symbol}</span>
        </Space>
      ),
    },
    {
      title: "状态",
      key: "status",
      width: 104,
      render: (_, item) => statusTag(item),
    },
    {
      title: "收盘",
      dataIndex: "close",
      key: "close",
      width: 84,
      align: "right",
      sorter: (a, b) => a.close - b.close,
      render: (value: number) => <NumberText value={value} />,
    },
    {
      title: "触发价 ≥",
      key: "price",
      width: 96,
      align: "right",
      render: (_, item) => (
        <b className="num">{item.nextTrigger?.price.toFixed(2) ?? "--"}</b>
      ),
    },
    {
      title: "还需涨",
      key: "needed",
      width: 96,
      align: "right",
      render: (_, item) =>
        item.nextTrigger ? <ChangeText value={item.nextTrigger.neededPct} /> : "--",
    },
    {
      title: "明日限幅",
      key: "limit",
      width: 96,
      align: "right",
      render: (_, item) =>
        item.nextTrigger ? (
          <span className="num reg-muted">±{item.nextTrigger.limitPct.toFixed(1)}%</span>
        ) : (
          "--"
        ),
    },
    {
      title: "基准",
      key: "benchmark",
      width: 90,
      render: (_, item) => <span className="reg-muted">{item.benchmarkName}</span>,
    },
  ];
  return (
    <Table<RegulatoryItem>
      rowKey="symbol"
      size="small"
      columns={columns}
      dataSource={items}
      pagination={false}
      scroll={{ x: 760 }}
      locale={{ emptyText: <Empty description="明日没有落在触发区间内的梯队股" /> }}
    />
  );
}

function ThresholdTable({ thresholds }: { thresholds: RegulatoryRadar["thresholds"] }) {
  return (
    <table className="reg-thresholds">
      <thead>
        <tr>
          <th>板块</th>
          <th>一般异动阈值（3 日累计偏离）</th>
          <th>ST 阈值</th>
          <th>日涨跌幅限制</th>
          <th>基准指数</th>
        </tr>
      </thead>
      <tbody>
        {thresholds.map((t) => (
          <tr key={`${t.exchange}-${t.board}`}>
            <td>
              {t.board}
              <span className="reg-muted"> · {EXCHANGE_TEXT[t.exchange] ?? t.exchange}</span>
            </td>
            <td className="num">±{t.thresholdPct.toFixed(0)}%</td>
            <td className="num">{t.stThresholdPct != null ? `±${t.stThresholdPct.toFixed(0)}%` : "--"}</td>
            <td className="num">
              {t.stPriceLimitPct != null
                ? `${t.priceLimitPct.toFixed(0)}%（ST ${t.stPriceLimitPct.toFixed(0)}%）`
                : `${t.priceLimitPct.toFixed(0)}%`}
            </td>
            <td>
              {t.benchmarkName} <span className="reg-muted">{t.benchmarkCode}</span>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export default function MarketRegulatoryPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [copied, setCopied] = useState(false);
  const dateParam = searchParams.get("date");
  const { data, isLoading, isFetching, error, refetch } = useQuery({
    queryKey: ["market", "regulatory-anomalies", dateParam ?? "latest"],
    queryFn: () => fetchRegulatoryRadar({ date: dateParam ?? undefined, historyDays: 10 }),
    // **切日期不能把整页打回骨架屏**：新 queryKey 没有缓存时保留上一帧，
    // 否则 data=undefined → StateWrapper 全页 spinner → 高度塌陷、滚动条跳动，
    // 用户看到的就是「点日历整个 web 抖一下」。加载态交给表格 overlay + 日历旁小 spinner。
    placeholderData: keepPreviousData,
    // 最新日与后端 Redis 缓存 TTL（120s）对齐；历史日不再轮询（EOD 结果不会变）。
    staleTime: dateParam ? Number.POSITIVE_INFINITY : 120_000,
    refetchInterval: dateParam ? false : 120_000,
  });

  // URL 是筛选状态的唯一真相：tab / status / board / q / symbol 都能分享与回退。
  const tab: "up" | "down" = searchParams.get("tab") === "down" ? "down" : "up";
  const statusParam = searchParams.get("status");
  const statusFilter: "all" | "triggered" | "near" =
    statusParam === "triggered" || statusParam === "near" ? statusParam : "all";
  const boardFilter = searchParams.get("board") ?? undefined;
  const query = searchParams.get("q") ?? "";
  const focusSymbol = searchParams.get("symbol");
  const ladderOnly = searchParams.get("ladder") === "1";

  const setParam = (key: string, value: string | null) => {
    const next = new URLSearchParams(searchParams);
    if (value) next.set(key, value);
    else next.delete(key);
    setSearchParams(next, { replace: true });
  };

  const clearFilters = () => {
    const next = new URLSearchParams(searchParams);
    ["q", "status", "board", "ladder", "symbol"].forEach((key) => next.delete(key));
    setSearchParams(next, { replace: true });
  };

  const directionItems = useMemo(
    () => (data?.items ?? []).filter((item) => item.direction === tab),
    [data, tab],
  );

  // 板块/搜索先筛（状态计的「全部/已达线/临近」以这个范围为分母，标签不会对不上）。
  const scopedItems = useMemo(() => {
    const keyword = query.trim().toLowerCase();
    return directionItems.filter((item) => {
      if (boardFilter && item.board !== boardFilter) return false;
      if (keyword && !`${item.symbol} ${item.name}`.toLowerCase().includes(keyword)) return false;
      if (
        ladderOnly &&
        !(
          item.limitUp ||
          item.limitDown ||
          (item.boardsInWindow ?? 0) >= 2 ||
          (item.streak ?? 0) > 0
        )
      ) {
        return false;
      }
      return true;
    });
  }, [directionItems, boardFilter, query, ladderOnly]);

  const counts = useMemo(
    () => ({
      all: scopedItems.length,
      triggered: scopedItems.filter((item) => item.status === "triggered").length,
      near: scopedItems.filter((item) => item.status === "near").length,
    }),
    [scopedItems],
  );

  const items = useMemo(
    () =>
      statusFilter === "all"
        ? scopedItems
        : scopedItems.filter((item) => item.status === statusFilter),
    [scopedItems, statusFilter],
  );

  const boardOptions = useMemo(
    () =>
      Array.from(new Set(directionItems.map((item) => item.board)))
        .sort((a, b) => a.localeCompare(b, "zh-CN"))
        .map((board) => ({ label: board, value: board })),
    [directionItems],
  );

  // 深链 `?symbol=`（自选卡片「异动雷达」入口）：只滚一次，避免用户随后输入搜索时
  // 每次 items 变化都被拽回同一行；仅换手率达标的票不在 table 里，回退到旁路 chip。
  const scrolledFor = useRef<string | null>(null);
  useEffect(() => {
    if (!focusSymbol || typeof document === "undefined") return;
    if (scrolledFor.current === focusSymbol) return;
    const row = document.querySelector(
      `tr[data-row-key="${CSS.escape(focusSymbol)}"]`,
    );
    const target =
      row ?? document.getElementById(`reg-turnover-${focusSymbol}`);
    if (!target) return;
    target.scrollIntoView({ block: "center", behavior: "smooth" });
    scrolledFor.current = focusSymbol;
  }, [focusSymbol, items]);

  const copyLink = async () => {
    try {
      await navigator.clipboard.writeText(window.location.href);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      // 剪贴板不可用（非安全上下文/权限拒绝）：不假装成功
    }
  };
  const preview = useMemo(
    () =>
      (data?.items ?? [])
        .filter(
          (item) =>
            item.direction === "up" &&
            item.status !== "triggered" &&
            item.nextTrigger != null &&
            item.nextTrigger.neededPct > 0,
        )
        .sort((a, b) => (a.nextTrigger?.neededPct ?? 0) - (b.nextTrigger?.neededPct ?? 0)),
    [data],
  );

  // 列定义无状态；提成常量避免每次筛选/输入都重建（也避免 AntD 的排序状态跟着重置）。
  const columns = useMemo(() => itemColumns(), []);

  const summary = data?.summary;
  const empty = !data || data.asOf == null;

  return (
    <div className="reg-page">
      <Breadcrumb items={[{ title: <Link to="/market">市场</Link> }, { title: "监管异动雷达" }]} />

      <div className="reg-hero">
        <div>
          <h1>监管异动雷达</h1>
          <Paragraph className="reg-sub">
            按交易所《交易规则》口径，用收盘价测算「连续 3 个交易日收盘价涨跌幅偏离值累计」：
            已达线 / 临近 / 明日再涨多少触发。换手率口径（3 日均换手 ≥ 前 5 日 30 倍且累计 ≥ 20%）
            另列「仅换手率达标」旁路，不与价格口径合并成一个数字。不构成投资建议。
          </Paragraph>
        </div>
        <div className="reg-asof">
          <div>
            {/* 日期左半边不再写死「收盘」：口径徽标就在旁边（`收盘 / 未完整 / 按最近完整日`），
                写死会在 complete 时变成「10月20日 收盘收盘」（2026-10-21 容器实测抓到）。 */}
            {data?.asOf ? formatAsOfDate(data.asOf) : "--"}
            {data ? <Tag className="reg-quality">{QUALITY_TEXT[data.asOfQuality] ?? data.asOfQuality}</Tag> : null}
          </div>
          <Space size={6} wrap>
            <DatePicker
              size="small"
              allowClear
              placeholder="最新交易日"
              value={dateParam ? dayjs(dateParam) : null}
              disabledDate={(current) => current.isAfter(dayjs(), "day")}
              onChange={(value) => setParam("date", value ? value.format("YYYY-MM-DD") : null)}
            />
            {dateParam ? (
              <Button size="small" type="link" onClick={() => setParam("date", null)}>
                回到最新
              </Button>
            ) : null}
            <span className="reg-fetching" aria-live="polite">
              {isFetching ? <Spin size="small" /> : null}
            </span>
            <span className="reg-muted">
              {dateParam ? "历史日期 · 不轮询" : "每 120 秒按缓存刷新"} · 价格口径 + 换手率旁路
            </span>
          </Space>
        </div>
      </div>

      <StateWrapper
        loading={isLoading}
        error={error as Error | null}
        empty={empty}
        emptyText={
          data?.degradedReason
            ? (DEGRADED_TEXT[data.degradedReason] ?? data.degradedReason)
            : "所选交易日没有可计算的行情"
        }
        onRetry={() => void refetch()}
      >
        {data ? (
          <>
            {data.degradedReason ? (
              <Alert
                type="warning"
                showIcon
                className="reg-alert"
                message={DEGRADED_TEXT[data.degradedReason] ?? data.degradedReason}
              />
            ) : null}

            <div className="reg-kpis">
              <Kpi label="涨向已达线" value={summary?.upTriggered ?? 0} tone="up" />
              <Kpi label="涨向临近" value={summary?.upNear ?? 0} tone="warn" />
              <Kpi label="跌向已达线" value={summary?.downTriggered ?? 0} tone="down" />
              <Kpi label="严重异常命中" value={summary?.severe ?? 0} tone="warn" />
              <Kpi label="今日涨停" value={summary?.limitUp ?? 0} />
              <Kpi label="今日跌停" value={summary?.limitDown ?? 0} />
            </div>

            <SectionCard
              title="今日梯队"
              asof={data.asOf}
              quality={data.asOfQuality === "requested" ? null : data.asOfQuality}
              staleDays={data.staleDays}
              note={tab === "up" ? "涨向" : "跌向"}
            >
              <div className="reg-toolbar">
                <Space wrap size={8}>
                  <Segmented
                    value={tab}
                    onChange={(value) => setParam("tab", value === "down" ? "down" : null)}
                    options={[
                      {
                        label: `涨向 ${summary?.upTriggered ?? 0} / ${summary?.upNear ?? 0}`,
                        value: "up",
                      },
                      {
                        label: `跌向 ${summary?.downTriggered ?? 0} / ${summary?.downNear ?? 0}`,
                        value: "down",
                      },
                    ]}
                  />
                  <Segmented
                    value={statusFilter}
                    onChange={(value) =>
                      setParam("status", value === "all" ? null : String(value))
                    }
                    options={[
                      { label: `全部 ${counts.all}`, value: "all" },
                      { label: `已达线 ${counts.triggered}`, value: "triggered" },
                      { label: `临近 ${counts.near}`, value: "near" },
                    ]}
                  />
                  <Select
                    allowClear
                    size="small"
                    className="reg-board-select"
                    placeholder="板块"
                    value={boardFilter}
                    onChange={(value) => setParam("board", value ?? null)}
                    options={boardOptions}
                  />
                  <Input
                    allowClear
                    size="small"
                    className="reg-search"
                    prefix={<SearchOutlined />}
                    placeholder="代码 / 名称"
                    aria-label="按代码或名称搜索"
                    value={query}
                    onChange={(event) => setParam("q", event.target.value)}
                  />
                  <Checkbox
                    checked={ladderOnly}
                    onChange={(event) => setParam("ladder", event.target.checked ? "1" : null)}
                  >
                    只看涨停/梯队
                  </Checkbox>
                </Space>
                <Space wrap size={8}>
                  <span className="reg-muted">
                    共 {items.length} 只 · 状态：已达线 = 3 日偏离 ≥ 阈值；临近 = ≥ 阈值 80%
                  </span>
                  <Button
                    size="small"
                    type="text"
                    icon={<ReloadOutlined />}
                    onClick={() => void refetch()}
                  >
                    刷新
                  </Button>
                  <Button
                    size="small"
                    type="text"
                    icon={<CopyOutlined />}
                    onClick={() => void copyLink()}
                  >
                    {copied ? "已复制" : "复制链接"}
                  </Button>
                </Space>
              </div>
              <Table<RegulatoryItem>
                rowKey="symbol"
                size="small"
                sticky={{ offsetHeader: 64 }}
                columns={columns}
                dataSource={items}
                // 有上一帧时用表格 overlay 表示加载，**不要**让整页回到 StateWrapper 的骨架。
                loading={isFetching && !isLoading}
                pagination={{ pageSize: 20, hideOnSinglePage: true, showSizeChanger: false }}
                scroll={{ x: 1180 }}
                rowClassName={(item) =>
                  [
                    item.status === "triggered" ? "reg-row--hot" : "",
                    item.symbol === focusSymbol ? "reg-row--focus" : "",
                  ]
                    .filter(Boolean)
                    .join(" ")
                }
                locale={{
                  emptyText: (
                    <Empty
                      description={
                        items.length === 0 && scopedItems.length > 0
                          ? "没有符合状态筛选的票"
                          : tab === "up"
                            ? "今日没有涨向的涨停/梯队异动股"
                            : "今日没有跌向的异动股"
                      }
                    >
                      <Button size="small" onClick={clearFilters}>
                        清除筛选
                      </Button>
                    </Empty>
                  ),
                }}
              />
              {data.turnoverOnly.length > 0 ? (
                <div className="reg-turnover" data-testid="reg-turnover-only">
                  <div className="reg-turnover__head">
                    仅换手率达标
                    <span className="reg-muted">
                      价格未达线 · 3 日日均换手 ≥ 前 5 日 30 倍且 3 日累计 ≥ 20%
                    </span>
                  </div>
                  <div className="reg-turnover__chips">
                    {data.turnoverOnly.map((item) => (
                      <Tooltip
                        key={item.symbol}
                        title={
                          `3日累计 ${item.turnover3dPct.toFixed(2)}% · ` +
                          `3日均 ${item.turnover3dAvgPct.toFixed(2)}% / ` +
                          `前5日均 ${item.turnoverPrev5AvgPct.toFixed(2)}% · ` +
                          `${item.turnoverRatio.toFixed(1)} 倍`
                        }
                      >
                        <Link
                          id={`reg-turnover-${item.symbol}`}
                          className="reg-chip reg-chip--turnover"
                          to={`/company/${item.symbol}`}
                        >
                          {item.name}
                          <b>{item.turnoverRatio.toFixed(0)}×</b>
                        </Link>
                      </Tooltip>
                    ))}
                  </div>
                </div>
              ) : data.turnoverAvailable === false ? (
                <div className="reg-muted reg-turnover__missing" data-testid="reg-turnover-missing">
                  换手率口径数据未更新（截至{" "}
                  {data.turnoverAsOf ? formatAsOfDate(data.turnoverAsOf) : "--"}），本段暂不判定。
                </div>
              ) : null}
            </SectionCard>

            <SectionCard
              title="明日触发预告"
              note="最近 2 日 + 明日"
              asof={data.asOf}
              quality={data.asOfQuality === "requested" ? null : data.asOfQuality}
              staleDays={data.staleDays}
            >
              <Paragraph className="reg-note">
                明日 3 日窗口 = 最近 2 个已知交易日 + 明日；触发价按「基准指数明日不涨不跌」估算，
                基准实际涨跌会等额移动触发价。
              </Paragraph>
              <PreviewTable items={preview.slice(0, 15)} />
              {preview.length > 15 ? (
                <div className="reg-muted reg-more">共 {preview.length} 只，按「还需涨幅」升序展示前 15 只。</div>
              ) : null}
            </SectionCard>

            <SectionCard title="近 10 个交易日异动" note="按首次越过阈值日">
              {data.history.length === 0 ? (
                <Empty description="近 10 个交易日没有股票越过阈值" />
              ) : (
                <div className="reg-history">
                  {data.history.map((day) => (
                    <div className="reg-history__day" key={day.tradeDate}>
                      <div className="reg-history__date">
                        {formatCnDate(day.tradeDate)}
                        <span className="reg-muted"> · {day.items.length} 只</span>
                      </div>
                      <div className="reg-history__chips">
                        {day.items.map((item) => (
                          <Tooltip
                            key={`${day.tradeDate}-${item.symbol}`}
                            // 不展示阈值：它按**今日**的 ST 快照算，回放到过去的触发日会错位
                            // （摘帽/戴帽后 12% 与 20%）。没有 PIT 名称表就不印这个数。
                            title={`${item.board} · 3 日偏离 ${item.dev3Pct.toFixed(2)}%`}
                          >
                            <Link
                              to={`/company/${item.symbol}`}
                              className={`reg-chip reg-chip--${item.direction}`}
                            >
                              {item.name}
                              <b>
                                {item.direction === "up" ? "+" : ""}
                                {item.dev3Pct.toFixed(2)}%
                              </b>
                            </Link>
                          </Tooltip>
                        ))}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </SectionCard>

            <SectionCard title="口径与已知失效场景">
              <div className="reg-docs">
                <div>
                  <h4>阈值与基准</h4>
                  <ThresholdTable thresholds={data.thresholds} />
                </div>
                <div>
                  <h4>这个页面不保证什么</h4>
                  <ul className="reg-caveats">
                    {data.caveats.map((caveat) => (
                      <li key={caveat}>{caveat}</li>
                    ))}
                  </ul>
                </div>
              </div>
            </SectionCard>
          </>
        ) : null}
      </StateWrapper>
    </div>
  );
}
