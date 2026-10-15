import { useParams, useNavigate, Link } from "react-router-dom";
import { Typography, Result, Button, Divider, Descriptions, Space, Spin, Breadcrumb } from "antd";
import { useQuery } from "@tanstack/react-query";
import { ArrowLeftOutlined } from "@ant-design/icons";
import { ChangeText } from "@/shared/ui";
import { formatAsOf } from "@/shared/ui/date";
import { KlineChart } from "@/shared/ui/kline";
import { fetchGlobalIndices } from "@/shared/api/marketData";
import { fetchIndexKline, fetchMarketIndices } from "@/shared/api/market";

/**
 * 详情页的展示模型。**三个行情字段可空**（与 `MarketIndex` 的必填数字不同）：
 *
 * 背景（Ruling 44）：本页对"既有指数列表里没有、但全球标的卡片里有"的代码（如本任务新增的
 * `NDX`/`SOX`/`HXC`）走 globalCards 兜底。这三个标的**按设计没有 `index_dailies` 日线行**，
 * 实时源不可用时 `price/change/pctChange` 是 `null`。旧写法 `card.price ?? 0` 会把 `null`
 * 变成 **`0`**——用户在卡片上看到 `—`，点进来看到 `0.00` 与 `+0.00%`：
 * 同一处数据两种说法，且 `0` 会被读成"平盘"（真实点位）。
 * 这条路径**只有本任务新增的标的会走到**，所以它属于本任务的收尾，不能留给下一个任务。
 */
interface IndexDetail {
  code: string;
  tsCode: string;
  name: string;
  value: number | null;
  change: number | null;
  changePercent: number | null;
  exchange: string;
  asof: string;
}

export default function IndexDetailPage() {
  const { tsCode } = useParams<{ tsCode: string }>();
  const navigate = useNavigate();

  const { data: indices = [], isLoading } = useQuery({
    queryKey: ["market", "indices"],
    queryFn: fetchMarketIndices,
    staleTime: 5 * 60 * 1000,
  });

  const { data: globalCards = [], isLoading: isGlobalLoading } = useQuery({
    queryKey: ["market", "global-indices"],
    queryFn: fetchGlobalIndices,
    staleTime: 60 * 1000,
  });

  const found = indices.find((idx) => idx.tsCode === tsCode);
  const index: IndexDetail | undefined =
    found ??
    (() => {
      const card = globalCards.find((c) => c.tsCode === tsCode);
      if (!card) return undefined;
      return {
        code: card.tsCode.split(".")[0],
        tsCode: card.tsCode,
        name: card.name,
        // ⚠️ 一律用 `?? null`，**不得** `?? 0`：缺价是"没有"，不是"零"。
        value: card.price ?? null,
        change: card.change ?? null,
        changePercent: card.pctChange ?? null,
        exchange: "Global",
        asof: card.updatedAt,
      };
    })();

  if (isLoading || isGlobalLoading) {
    return (
      <div style={{ display: "flex", justifyContent: "center", padding: "64px 0" }}>
        <Spin size="large" />
      </div>
    );
  }

  if (!index) {
    return (
      <Result
        status="404"
        title="未找到该指数"
        subTitle={`指数代码 ${tsCode ?? ""} 不存在或暂无数据`}
        extra={
          <Button type="primary" onClick={() => navigate("/market")}>
            返回市场
          </Button>
        }
      />
    );
  }

  return (
    <div>
      {/* 层级式面包屑：市场 / {指数名}（末项为当前页不可点击） */}
      <Breadcrumb
        style={{ marginBottom: 8 }}
        items={[
          { title: <Link to="/market">市场</Link> },
          { title: index.name },
        ]}
      />
      <Space align="center" size={12} style={{ marginBottom: 16 }}>
        <Button
          type="text"
          icon={<ArrowLeftOutlined />}
          onClick={() => navigate("/market")}
        />
        <div>
          <Space align="center" size={8}>
            <Typography.Title level={3} style={{ margin: 0 }}>
              {index.name}
            </Typography.Title>
            <Typography.Text type="secondary" style={{ fontFamily: "monospace" }}>
              {index.tsCode}
            </Typography.Text>
          </Space>
          <div style={{ marginTop: 4, display: "flex", alignItems: "baseline", gap: 16 }}>
            {/* 缺价 → `—`（与全球卡片同一口径）；**不渲染 0**，0 会被读成真实点位/平盘。
                `ChangeText` 的 null 分支渲染 `--`，与这里的 `—` 同义（都是"不可判"）。 */}
            {index.value == null ? (
              <span style={{ fontSize: 28, fontWeight: 700, color: "var(--text-secondary)" }}>
                —
              </span>
            ) : (
              <span style={{ fontSize: 28, fontWeight: 700 }}>
                {index.value.toLocaleString("zh-CN", { minimumFractionDigits: 2 })}
              </span>
            )}
            <ChangeText value={index.change} suffix="" style={{ fontSize: 16 }} />
            <ChangeText value={index.changePercent} style={{ fontSize: 16 }} />
          </div>
          {index.value == null && (
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              该标的暂无可用行情（既无实时快照也无日线），故不显示数值；行情源接入后会自动出现。
            </Typography.Text>
          )}
        </div>
      </Space>

      <Descriptions size="small" column={3} style={{ marginBottom: 8 }}>
        {/* 日期口径唯一入口（§3.9）：`asof` 是 ISO 时间戳（`2026-10-13T15:00:00Z`），
            **不得**原样上屏（Phase 0 实测本页就是 0.6 运行时门禁的红）。
            `formatAsOf` → 「10月13日收盘 · 2天前」；静态与运行时两层门禁见 §8.2。 */}
        <Descriptions.Item label="数据截至">{formatAsOf(index.asof)}</Descriptions.Item>
      </Descriptions>

      <Divider style={{ margin: "16px 0" }} />

      {tsCode && (
        <KlineChart
          title="指数历史行情"
          queryKey={`index-kline-${tsCode}`}
          fetcher={(days) => fetchIndexKline(tsCode, days)}
        />
      )}

      <Divider style={{ margin: "16px 0" }} />

      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
        本页数据仅供参考，不构成投资建议。数据来源可能存在延迟，请以交易所实时数据为准。
      </Typography.Text>
    </div>
  );
}
