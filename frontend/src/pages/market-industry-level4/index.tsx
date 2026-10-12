import { useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { Alert, Breadcrumb, Card, Empty, Space, Table, Tag, Typography } from "antd";
import type { TableProps } from "antd";
import { useQuery } from "@tanstack/react-query";
import { DeltaText } from "@/shared/ui/DeltaText";
import { fetchIndustryMembers, type IndustryMember } from "@/shared/api/swDrilldown";
import { IndustryAsOfBar, IndustryMetricStrip } from "@/features/market/components/IndustryMetricStrip";

/**
 * S1.4 末级页：`/market/industry/:level1Code/:level2Code/:level3Code`
 * = 该三级行业的 7 项聚合 + 成分股逐只 7 项字段。
 *
 * 成分按 **SCD2** 还原（`as_of` 当日有效的 `effective_from`/`effective_to` 区间），
 * 每行都显示自己所属的区间——"什么时候算这个行业的成分"是这一页的一等信息，
 * 不只是审计字段。
 *
 * 权限：`/market/**` 公开免登录（ADR 0008），本页**不挂** RequireAuth。
 */

const DASH = "--";

function fmtNum(v: number | null, digits = 2): string {
  return v == null ? DASH : v.toFixed(digits);
}

function fmtVolume(v: number | null): string {
  if (v == null) return DASH;
  if (Math.abs(v) >= 1e8) return `${(v / 1e8).toFixed(2)}亿`;
  if (Math.abs(v) >= 1e4) return `${(v / 1e4).toFixed(2)}万`;
  return v.toFixed(0);
}

function fmtAmountYuan(v: number | null): string {
  if (v == null) return DASH;
  if (Math.abs(v) >= 1e8) return `${(v / 1e8).toFixed(2)}亿`;
  if (Math.abs(v) >= 1e4) return `${(v / 1e4).toFixed(2)}万`;
  return v.toFixed(0);
}

export default function IndustryLevel4Page() {
  const navigate = useNavigate();
  const { level1Code = "", level2Code = "", level3Code = "" } = useParams();
  const [sort, setSort] = useState<{ by: keyof IndustryMember | null; order: "asc" | "desc" }>({
    by: null,
    order: "asc",
  });
  /**
   * 历史口径日（Fix round 1 · 评审 Minor）：此前**没有任何前端路径**能传 `asOf`，
   * 后端"按 SCD2 区间还原历史成分"的能力在 UI 上完全不可达。空 = 最新完整交易日。
   */
  const [asOf, setAsOf] = useState<string | null>(null);

  const { data, isLoading, error } = useQuery({
    queryKey: ["sw-drill-members", level1Code, level2Code, level3Code, asOf],
    queryFn: () =>
      fetchIndustryMembers({
        level1Code,
        level2Code,
        level3Code,
        asOf: asOf ?? undefined,
      }),
    enabled: Boolean(level1Code && level2Code && level3Code),
    staleTime: 30_000,
  });

  const rows = useMemo(() => {
    const items = data?.items ?? [];
    if (!sort.by) return items;
    const dir = sort.order === "asc" ? 1 : -1;
    return [...items].sort((a, b) => {
      const av = a[sort.by as keyof IndustryMember];
      const bv = b[sort.by as keyof IndustryMember];
      if (av == null && bv == null) return 0;
      if (av == null) return 1; // 缺失恒排在后，不当作 0 参与比较
      if (bv == null) return -1;
      return av > bv ? dir : av < bv ? -dir : 0;
    });
  }, [data?.items, sort]);

  const onTableChange: TableProps<IndustryMember>["onChange"] = (_p, _f, sorter) => {
    if (!Array.isArray(sorter) && sorter.field) {
      setSort({
        by: sorter.field as keyof IndustryMember,
        order: sorter.order === "ascend" ? "asc" : "desc",
      });
    } else {
      setSort({ by: null, order: "asc" });
    }
  };

  const chain = data?.chain ?? [];
  const title = chain.length ? chain[chain.length - 1].name : level3Code;

  const columns: TableProps<IndustryMember>["columns"] = [
    {
      title: "代码",
      dataIndex: "symbol",
      key: "symbol",
      fixed: "left",
      render: (symbol: string) => <Link to={`/company/${symbol}`}>{symbol}</Link>,
    },
    { title: "名称", dataIndex: "name", key: "name", render: (v: string | null) => v ?? DASH },
    {
      title: "开/高/低/收",
      key: "kline",
      render: (_v, row) =>
        `${fmtNum(row.open)} / ${fmtNum(row.high)} / ${fmtNum(row.low)} / ${fmtNum(row.close)}`,
    },
    {
      title: "涨跌幅",
      dataIndex: "pct_chg",
      key: "pct_chg",
      sorter: true,
      render: (v: number | null) => <DeltaText value={v} />,
    },
    {
      title: "换手率",
      dataIndex: "turnover_rate",
      key: "turnover_rate",
      sorter: true,
      render: (v: number | null) => `${fmtNum(v)}%`,
    },
    {
      title: "成交量",
      dataIndex: "volume",
      key: "volume",
      sorter: true,
      render: (v: number | null) => fmtVolume(v),
    },
    {
      title: "量比",
      dataIndex: "volume_ratio",
      key: "volume_ratio",
      sorter: true,
      render: (v: number | null) => fmtNum(v),
    },
    {
      title: "振幅",
      dataIndex: "amplitude",
      key: "amplitude",
      sorter: true,
      render: (v: number | null) => `${fmtNum(v)}%`,
    },
    {
      title: "成交额",
      dataIndex: "amount",
      key: "amount",
      sorter: true,
      render: (v: number | null) => fmtAmountYuan(v),
    },
    {
      title: "归属区间（SCD2）",
      key: "period",
      render: (_v, row) => (
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          {row.effective_from} ~ {row.effective_to ?? "至今"}
        </Typography.Text>
      ),
    },
  ];

  return (
    <Space direction="vertical" size={16} style={{ width: "100%" }}>
      <Breadcrumb
        items={[
          { title: <Link to="/market">市场</Link> },
          { title: <Link to={`/market/industry/${level1Code}`}>{chain[0]?.name ?? level1Code}</Link> },
          {
            title: (
              <Link to={`/market/industry/${level1Code}/${level2Code}`}>
                {chain[1]?.name ?? level2Code}
              </Link>
            ),
          },
          { title: title },
        ]}
      />

      {error ? (
        <Alert
          type="error"
          showIcon
          message="行业成分读取失败"
          description={error instanceof Error ? error.message : undefined}
        />
      ) : null}

      {data ? (
        <IndustryAsOfBar
          asOf={data.as_of}
          asOfQuality={data.as_of_quality}
          asOfReason={data.as_of_reason}
          scd2AsOf={data.scd2_as_of}
          scd2Available={data.scd2_available}
          scd2Note={data.scd2_note}
          scd2Coverage={data.scd2_coverage}
          onPickDay={setAsOf}
        />
      ) : null}

      {data && !data.scd2_available ? (
        <Alert type="warning" showIcon message="分类归属尚无 SCD2 数据" description={data.scd2_note} />
      ) : null}

      <Card title={`${title} · 7 项行情字段（行业聚合）`} size="small">
        <IndustryMetricStrip metrics={data?.metrics ?? null} />
      </Card>

      <Card
        title={`${title} · 成分股（${rows.length}）`}
        size="small"
        extra={<Tag color="geekblue">按 {data?.scd2_as_of ?? "—"} 的分类归属还原</Tag>}
      >
        {rows.length ? (
          <Table<IndustryMember>
            rowKey="symbol"
            size="small"
            columns={columns}
            dataSource={rows}
            loading={isLoading}
            onChange={onTableChange}
            pagination={{ pageSize: 50, showSizeChanger: false }}
            scroll={{ x: 1100 }}
          />
        ) : (
          <Empty
            description={
              isLoading
                ? "加载中…"
                : data?.scd2_available
                  ? "该三级行业在所选归属口径日无成分股"
                  : "分类归属数据尚未接入（不是「当日无成分」）"
            }
          />
        )}
      </Card>

      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
        返回{" "}
        <a onClick={() => navigate(`/market/industry/${level1Code}/${level2Code}`)}>二级行业</a>
      </Typography.Text>
    </Space>
  );
}
