/**
 * 全球市场 `/market/global`（S1.3 段 A）。
 *
 * 数据来源唯一：`GET /api/v1/market/global-indices`（后端登记表 `GLOBAL_INDICES`，
 * 五要素 name / ts_code / session / timezone / currency 全量下发）。
 *
 * **本页不持有任何标的清单的第二份副本**：分组按后端给的 `market` 字段动态聚合——
 * 前端再抄一份"美国 6 / 香港 2 / 其他 3"，下一次后端加标的就会出现两份会漂移的真相。
 *
 * **段 B（大宗商品 29 / 外汇 5）在此只做「数据源待接入」占位**：其可行性调研见
 * `docs/design/data-source.md` 附.5，**未验证前不写适配器、不填数字、不画空图**。
 * 故这里没有这两块的标的清单——因为清单本身要等数据源定下来才有意义。
 */
import { useMemo, useState } from "react";
import { Breadcrumb, Card, Col, Row, Segmented, Space, Table, Tag, Typography } from "antd";
import { useQuery } from "@tanstack/react-query";
import type { ColumnsType } from "antd/es/table";
import { Link } from "react-router-dom";
import { DegradedNotice, SectionCard } from "@/shared/ui";
import { fetchGlobalIndices, type GlobalIndexCard } from "@/shared/api/marketData";
import { DataSourceBadge, PriceSourceBadge } from "@/features/market/components/DataSourceBadge";
import { useMarketPolling } from "@/features/market/hooks/useMarketPolling";

/** 市场分组顺序：文档/导图口径（美 / 港 / 其他），CN 放最后（海外视图里它是参照系）。 */
const MARKET_ORDER = ["US", "HK", "JP", "KR", "SG", "CN"] as const;

const MARKET_LABEL: Record<string, string> = {
  US: "美国市场",
  HK: "香港市场",
  JP: "日本市场",
  KR: "韩国市场",
  SG: "新加坡市场",
  CN: "A 股市场（参照）",
};

function marketLabel(market: string): string {
  return MARKET_LABEL[market] ?? `${market} 市场`;
}

/**
 * 各市场在「交易时段」列上额外要标的**市场身份**标签（按 `row.market` 取，不按 `tsCode`）。
 *
 * 为什么按市场而不是按标的：标签要回答的是"这行属不属于 A 股那套交易时段"，
 * 那是**市场级**属性。绑在字面量 `tsCode` 上时，第二个 SG 标的会静默丢掉标记，
 * 且元数据改了之后页面依旧宣称旧标签（"文档与实现两处真相"那类腐烂）。
 *
 * A50（富时中国A50 指数期货）：`market=SG`、`currency=CNY`、时段是 T+1 夜盘。
 * 导图点名要求它在页面上显式可见，避免与 A 股交易时段混淆。
 */
const MARKET_MARKER: Record<string, (row: GlobalIndexCard) => React.ReactNode> = {
  SG: (row) => (
    <Space size={4}>
      <Tag color="orange" style={{ marginInlineEnd: 0 }}>
        新加坡
      </Tag>
      {row.currency === "CNY" && (
        <Tag color="volcano" style={{ marginInlineEnd: 0 }}>
          CNY 夜盘
        </Tag>
      )}
    </Space>
  ),
};

function fmtPrice(v: number | null): string {
  if (v == null) return "—"; // 「没有」显示破折号，绝不补 0（0 会被读成"平盘"）
  return v.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

/**
 * 涨跌渲染：null → `—`（不可判），不 0 填充。
 *
 * 颜色走**主题 CSS 变量**（`--up`/`--down`/`--flat`，定义见 `app/styles/theme.css`），
 * 与 `SentimentThermometer.css` / `GlobalIndexCardView` 同一口径。
 * **不得写死 hex**：审查实测 `#cf1322` 在暗色面板 `#1e222d` 上只有 ≈2.8:1，
 * 低于仓库要求的 4.5:1（暗色 `--up` = `#f2555a` → 4.71:1）；而 `check:design`
 * 只校验主题 token 自身，**抓不到组件里的写死色值**，所以这条只能靠"用变量"保证。
 */
function PctText({ card }: { card: GlobalIndexCard }) {
  if (card.pctChange == null) return <span>—</span>;
  const color =
    card.pctChange > 0 ? "var(--up)" : card.pctChange < 0 ? "var(--down)" : "var(--flat)";
  return (
    <span style={{ color, fontVariantNumeric: "tabular-nums" }}>
      {card.pctChange > 0 ? "+" : ""}
      {card.pctChange.toFixed(2)}%
      {card.change != null && (
        <span style={{ marginLeft: 6, fontWeight: 400 }}>
          {card.change > 0 ? "+" : ""}
          {card.change.toFixed(2)}
        </span>
      )}
    </span>
  );
}

const columns: ColumnsType<GlobalIndexCard> = [
  {
    title: "名称 / 代码",
    dataIndex: "name",
    key: "name",
    width: 220,
    render: (_: unknown, row) => (
      <Space direction="vertical" size={0}>
        <Typography.Text strong style={{ fontSize: 13 }}>
          {row.name}
        </Typography.Text>
        <Typography.Text type="secondary" style={{ fontSize: 11, fontFamily: "monospace" }}>
          {row.tsCode}
        </Typography.Text>
      </Space>
    ),
  },
  {
    title: "最新值",
    key: "price",
    align: "right",
    width: 150,
    render: (_: unknown, row) => (
      <Space direction="vertical" size={0} style={{ alignItems: "flex-end" }}>
        <span style={{ fontVariantNumeric: "tabular-nums", fontWeight: 600 }}>
          {fmtPrice(row.price)}
        </span>
        <span style={{ fontSize: 10.5 }}>
          <PriceSourceBadge priceSource={row.priceSource} />
        </span>
      </Space>
    ),
  },
  {
    title: "涨跌幅",
    key: "pct",
    align: "right",
    width: 140,
    render: (_: unknown, row) => <PctText card={row} />,
  },
  {
    // S1.3 五要素里的三项逐列展示（名称与代码在首列）——UI 上"看得见"才算交付
    title: "交易时段",
    dataIndex: "session",
    key: "session",
    width: 260,
    render: (session: string, row) => (
      <Space direction="vertical" size={2}>
        <Typography.Text style={{ fontSize: 12 }}>{session}</Typography.Text>
        {/* 导图点名要求：A50 必须在页面上显式标出「新加坡 · CNY 夜盘」。
            标记**按 `row.market` 派生**（不按字面量 tsCode）：`tsCode === "XIN9"` 会让
            第二个 SG 标的静默丢掉标记，也会在元数据改了之后继续宣称"CNY 夜盘"。
            币种标签同样取 `row.currency`，不写死。 */}
        {MARKET_MARKER[row.market]?.(row)}
      </Space>
    ),
  },
  { title: "时区", dataIndex: "timezone", key: "timezone", width: 150 },
  {
    title: "币种",
    dataIndex: "currency",
    key: "currency",
    width: 80,
    render: (c: string) => <Tag>{c}</Tag>,
  },
  {
    title: "数据源代码",
    dataIndex: "dataSource",
    key: "dataSource",
    width: 150,
    render: (s: string) => <DataSourceBadge source={s} />,
  },
];

/**
 * 段 B 占位：大宗商品 / 外汇。**只有一句"待接入"与依据**，没有标的清单、没有图表、没有数字。
 *
 * 为什么不列出 29 / 5 个名字：名字本身要等数据源定下来（不同源的品种编码不同），
 * 现在抄一份名字清单＝凭空造一份将来必然要作废的契约。数量口径以
 * [S0.4 §4.1](../../../../docs/specs/S0-platform-contracts.md) 复核结论为准。
 */
function PendingSourceCard({
  title,
  count,
  candidates,
  reason,
}: {
  title: string;
  count: number;
  candidates: string;
  reason: string;
}) {
  return (
    <Card size="small" title={title}>
      <Space direction="vertical" size={6}>
        <Space size={6} wrap>
          <Tag color="default">数据源待接入</Tag>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            口径数量 {count} 个标的
          </Typography.Text>
        </Space>
        <Typography.Text style={{ fontSize: 12 }}>{reason}</Typography.Text>
        <Typography.Text type="secondary" style={{ fontSize: 11.5 }}>
          候选源：{candidates}
        </Typography.Text>
        <Typography.Text type="secondary" style={{ fontSize: 11.5 }}>
          可行性调研（三态结论与验证方法）见仓库文档 docs/design/data-source.md 附.5。
          <strong>接入前不放任何数值或图表</strong>——本页宁缺勿假。
        </Typography.Text>
      </Space>
    </Card>
  );
}

export default function MarketGlobalPage() {
  const { refetchInterval } = useMarketPolling("global-index");
  const [market, setMarket] = useState<string>("all");
  const { data: indices = [], isLoading, error } = useQuery({
    queryKey: ["market", "global-indices"],
    queryFn: fetchGlobalIndices,
    staleTime: 60 * 1000,
    refetchInterval,
  });

  /** 分组由后端 `market` 字段聚合；顺序按 MARKET_ORDER，未登记的市场排在其后（不丢数据）。 */
  const groups = useMemo(() => {
    const byMarket = new Map<string, GlobalIndexCard[]>();
    for (const card of indices) {
      const list = byMarket.get(card.market) ?? [];
      list.push(card);
      byMarket.set(card.market, list);
    }
    const known = MARKET_ORDER.filter((m) => byMarket.has(m));
    const unknown = [...byMarket.keys()].filter(
      (m) => !(MARKET_ORDER as readonly string[]).includes(m)
    );
    return [...known, ...unknown].map((m) => ({ market: m, items: byMarket.get(m) ?? [] }));
  }, [indices]);

  const visible = market === "all" ? groups : groups.filter((g) => g.market === market);

  return (
    <div className="market-page" data-testid="market-global-page">
      <Breadcrumb
        items={[{ title: <Link to="/market">行情中心</Link> }, { title: "全球市场" }]}
        style={{ marginBottom: 12 }}
      />
      <div className="market-page-head" style={{ marginBottom: 12 }}>
        <Typography.Title level={4} style={{ margin: 0 }}>
          全球市场与跨市场标的
        </Typography.Title>
        <Typography.Text type="secondary" style={{ fontSize: 13 }}>
          美股 / 港股 / 其他市场指数 · 每条标的带齐「名称 + 数据源代码 + 交易时段 + 时区 + 币种」
        </Typography.Text>
      </div>

      <Row gutter={[16, 16]} style={{ marginBottom: 16 }}>
        <Col span={24}>
          <Card size="small">
            <Space direction="vertical" size={4}>
              <Segmented
                data-testid="market-global-filter"
                value={market}
                onChange={(v) => setMarket(String(v))}
                options={[
                  { label: `全部（${indices.length}）`, value: "all" },
                  ...groups.map((g) => ({
                    label: `${marketLabel(g.market)}（${g.items.length}）`,
                    value: g.market,
                  })),
                ]}
              />
              <Typography.Text type="secondary" style={{ fontSize: 11.5 }}>
                富时中国A50 指数期货（新加坡，T+1 夜盘）<strong>不在 A 股交易时段</strong>交易，已单独标注时段与币种，勿与 A 股混看。
              </Typography.Text>
            </Space>
          </Card>
        </Col>
      </Row>

      {error ? (
        <Card size="small">
          <DegradedNotice reason={`全球指数读取失败：${(error as Error).message}`} />
        </Card>
      ) : null}

      {/* 空态：端点正常但返回 0 条（不是错误）。没有它页面只剩「全部（0）」与段 B 占位，
          用户分不清"这一波还没接"与"接口挂了"。空图/0 一律不画，只给一句话。 */}
      {!error && !isLoading && indices.length === 0 ? (
        <Card size="small" style={{ marginBottom: 16 }}>
          <Typography.Text type="secondary">
            暂无全球标的元数据（接口返回 0 条）。这不是"没有行情"，而是标的登记表为空或读面未就绪——
            请查 <code>GET /api/v1/market/global-indices</code> 与后端登记表日志。
          </Typography.Text>
        </Card>
      ) : null}

      {visible.map((group) => (
        <Row key={group.market} gutter={[16, 16]} style={{ marginBottom: 16 }}>
          <Col span={24}>
            <SectionCard title={marketLabel(group.market)}>
              <Table<GlobalIndexCard>
                rowKey="tsCode"
                size="small"
                loading={isLoading}
                columns={columns}
                dataSource={group.items}
                pagination={false}
                scroll={{ x: 1100 }}
              />
            </SectionCard>
          </Col>
        </Row>
      ))}

      <Row gutter={[16, 16]}>
        <Col xs={24} xl={12}>
          <PendingSourceCard
            title="大宗商品"
            count={29}
            candidates="AKShare futures_zh_daily_sina / futures_foreign_* / spot_*（候选，未通过验证）"
            reason="本轮只做数据源可行性调研，未写适配器、未建表；三态结论见调研附录。"
          />
        </Col>
        <Col xs={24} xl={12}>
          <PendingSourceCard
            title="外汇市场"
            count={5}
            candidates="TuShare fx_daily（本仓库客户端未接入）/ AKShare forex_* / currency_boc_sina（候选，未通过验证）"
            reason="本轮只做数据源可行性调研，未写适配器、未建表；三态结论见调研附录。"
          />
        </Col>
      </Row>
    </div>
  );
}
