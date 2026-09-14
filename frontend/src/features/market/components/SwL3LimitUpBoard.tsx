import { useMemo, useState } from "react";
import { Segmented, Table, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useNavigate } from "react-router-dom";
import type { SectorLimitUp, SectorLimitUpItem } from "@/shared/api/limitUp";

interface Props {
  data: SectorLimitUp | null | undefined;
  /** 端点降级（如 partial_day）时渲染短占位文案，不展示不完整数据。 */
  degraded?: boolean;
}

const ALL = "all";

/**
 * 申万三级最高板：按细分行业聚合的最高板/龙头/涨停家数。
 * 顶部 Segmented 客户端按一级行业（l1Code）过滤，数据来自已加载的 `items`。
 * 行点击跳个股页 `/stock/:symbol`（与 StockTable/DragonTigerTable 同路由）。
 */
export function SwL3LimitUpBoard({ data, degraded = false }: Props) {
  const navigate = useNavigate();
  const [l1, setL1] = useState<string>(ALL);

  const l1Options = useMemo(() => {
    const seen = new Map<string, string>();
    for (const item of data?.items ?? []) {
      if (item.l1Code && !seen.has(item.l1Code)) {
        seen.set(item.l1Code, item.l1Name ?? item.l1Code);
      }
    }
    return [...seen.entries()].map(([code, name]) => ({ label: name, value: code }));
  }, [data]);

  const rows = useMemo(() => {
    const items = data?.items ?? [];
    return l1 === ALL ? items : items.filter((i) => i.l1Code === l1);
  }, [data, l1]);

  const columns: ColumnsType<SectorLimitUpItem> = [
    {
      title: "细分行业",
      dataIndex: "l3Name",
      render: (_, r) => r.l3Name ?? "--",
    },
    {
      title: "最高板",
      dataIndex: "maxStreak",
      align: "right",
      width: 80,
      render: (_, r) => `${r.maxStreak}板`,
    },
    {
      title: "龙头",
      dataIndex: "leaderName",
      render: (_, r) => r.leaderName ?? "--",
    },
    {
      title: "涨停家数",
      dataIndex: "ztCount",
      align: "right",
      width: 90,
    },
    {
      title: "一级行业",
      dataIndex: "l1Name",
      ellipsis: true,
      render: (_, r) => r.l1Name ?? "--",
    },
  ];

  if (degraded) {
    return (
      <div className="sector-limit-up">
        <Typography.Text
          type="secondary"
          style={{ display: "block", padding: "24px 0", textAlign: "center" }}
        >
          数据不完整，暂不展示板块板高
        </Typography.Text>
      </div>
    );
  }

  return (
    <div className="sector-limit-up">
      <Segmented
        block
        value={l1}
        options={[{ label: "全部", value: ALL }, ...l1Options]}
        onChange={(v) => setL1(v as string)}
        style={{ marginBottom: 12 }}
      />
      <Table<SectorLimitUpItem>
        rowKey="l3Code"
        size="small"
        columns={columns}
        dataSource={rows}
        pagination={false}
        scroll={{ x: 560 }}
        locale={{ emptyText: "无申万三级涨停数据" }}
        onRow={(r) => ({
          style: { cursor: "pointer" },
          onClick: () => navigate(`/stock/${r.leaderSymbol}`),
        })}
      />
    </div>
  );
}
