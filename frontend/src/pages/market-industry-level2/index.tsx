import { useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { Alert, Breadcrumb, Card, Col, Empty, Row, Space, Tag, Typography } from "antd";
import type { TableProps } from "antd";
import { useQuery } from "@tanstack/react-query";
import { StockTable } from "@/features/market/components/StockTable";
import type { StockRecord } from "@/shared/types";
import {
  customTagSuffix,
  fetchSwIndustryTree,
  fetchSwLevel1Stocks,
  fetchSwLevel1StocksEnriched,
} from "@/shared/api/swIndustry";
import { fetchIndustryLevel } from "@/shared/api/swDrilldown";
import {
  IndustryAsOfBar,
  IndustryMetricStrip,
} from "@/features/market/components/IndustryMetricStrip";

type SortState = {
  sortBy?: keyof StockRecord;
  sortOrder?: "asc" | "desc";
};

function applySort(stocks: StockRecord[], sort: SortState): StockRecord[] {
  if (!sort.sortBy) return stocks;
  const sorted = [...stocks];
  const direction = sort.sortOrder === "asc" ? 1 : -1;
  sorted.sort((a, b) => {
    const av = a[sort.sortBy!] ?? 0;
    const bv = b[sort.sortBy!] ?? 0;
    return av > bv ? direction : av < bv ? -direction : 0;
  });
  return sorted;
}

export default function IndustryLevel2Page() {
  const navigate = useNavigate();
  const { level1Code = "" } = useParams();
  const [sort, setSort] = useState<SortState>({ sortBy: "symbol", sortOrder: "asc" });

  const { data: tree = [], isLoading: treeLoading } = useQuery({
    queryKey: ["market", "sw-industry-tree"],
    queryFn: fetchSwIndustryTree,
  });

  // ── Fast: basic stock metadata (renders immediately) ──
  const { data: basicStocks = [], isLoading: stocksLoading } = useQuery({
    queryKey: ["sw-level1-stocks", level1Code],
    queryFn: () => fetchSwLevel1Stocks(level1Code),
    enabled: Boolean(level1Code),
  });

  // ── Deferred: quote + daily_basic (fills financial columns async) ──
  const { data: enrichedStocks = [] } = useQuery({
    queryKey: ["sw-level1-stocks-enriched", level1Code],
    queryFn: () => fetchSwLevel1StocksEnriched(level1Code),
    enabled: Boolean(level1Code && basicStocks.length > 0),
    staleTime: 30_000,
    refetchOnWindowFocus: false,
  });

  const level1 = useMemo(() => tree.find((node) => node.code === level1Code), [tree, level1Code]);

  // ── S1.4：该一级行业下各二级行业的 7 项聚合（SCD2 归属口径随响应外露）──
  // 与上面的 tree 查询并行：tree 给静态层级与成分数，drill 给行情字段。
  // drill 失败不阻断页面（只少一条指标带），故不把它放进 error 分支。
  const { data: drill } = useQuery({
    queryKey: ["sw-drill-levels", 2, level1Code],
    queryFn: () => fetchIndustryLevel(2, { level1Code }),
    enabled: Boolean(level1Code),
    staleTime: 60_000,
  });
  const metricsByCode = useMemo(
    () => new Map((drill?.items ?? []).map((it) => [it.code, it.metrics])),
    [drill?.items]
  );

  const displayStocks = useMemo(() => {
    const enrichedMap = new Map(enrichedStocks.map((s) => [s.symbol, s]));
    const merged = basicStocks.map((s) => enrichedMap.get(s.symbol) ?? s);
    return applySort(merged, sort);
  }, [basicStocks, enrichedStocks, sort]);

  const onTableChange: TableProps<StockRecord>["onChange"] = (_pagination, _filters, sorter) => {
    if (!Array.isArray(sorter) && sorter.field) {
      setSort({
        sortBy: sorter.field as keyof StockRecord,
        sortOrder: sorter.order === "ascend" ? "asc" : "desc",
      });
    }
  };

  if (!level1) {
    return (
      <Card>
        {treeLoading ? <Typography.Text type="secondary">行业数据加载中...</Typography.Text> : <Empty description="未找到对应的一级行业" />}
      </Card>
    );
  }

  const handleL2Click = (l2Code: string) => {
    navigate(`/market/industry/${level1.code}/${l2Code}`);
  };

  return (
    <Space direction="vertical" size={16} style={{ width: "100%" }}>
      <Breadcrumb
        items={[
          { title: <Link to="/market">市场</Link> },
          { title: <Link to="/market/industry">申万一级</Link> },
          { title: level1.name },
        ]}
      />

      {drill ? (
        <IndustryAsOfBar
          asOf={drill.as_of}
          asOfQuality={drill.as_of_quality}
          asOfReason={drill.as_of_reason}
          scd2AsOf={drill.scd2_as_of}
          scd2Available={drill.scd2_available}
          scd2Note={drill.scd2_note}
          scd2Coverage={drill.scd2_coverage}
        />
      ) : null}

      {drill && !drill.scd2_available ? (
        <Alert type="warning" showIcon message="分类归属尚无 SCD2 数据" description={drill.scd2_note} />
      ) : null}

      {drill?.parent ? (
        <Card title={`${level1.name} · 7 项行情字段（一级聚合）`} size="small">
          <IndustryMetricStrip metrics={drill.parent.metrics} />
        </Card>
      ) : null}

      <Card
        title={`${level1.name} · 二级行业`}
        size="small"
        extra={
          <Typography.Text type="secondary">
            点击二级行业进入详情页面
          </Typography.Text>
        }
      >
        {level1.children.length === 0 ? (
          <Empty description="暂无子分组" />
        ) : (
          <Row gutter={[12, 12]}>
            {level1.children.map((level2) => (
              <Col key={level2.code} xs={24} sm={12} lg={8}>
                <Card
                  hoverable
                  size="small"
                  onClick={() => handleL2Click(level2.code)}
                >
                  <Space direction="vertical" size={8} style={{ width: "100%" }}>
                    <Typography.Text strong>{level2.name}</Typography.Text>
                    <Space size={8}>
                      {/* 官方成分数（SCD2，与下钻页逐节点一致）；自定义标签只作标注，不相加 */}
                      <Tag color="blue">{level2.officialCount} 只个股</Tag>
                      {customTagSuffix(level2) ? (
                        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                          {customTagSuffix(level2)}
                        </Typography.Text>
                      ) : null}
                      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                        {level2.children.length} 个三级行业
                      </Typography.Text>
                    </Space>
                    {metricsByCode.get(level2.code) ? (
                      <IndustryMetricStrip metrics={metricsByCode.get(level2.code) ?? null} compact />
                    ) : null}
                  </Space>
                </Card>
              </Col>
            ))}
          </Row>
        )}
      </Card>

      <Card
        title={`${level1.name} · 个股最新信息`}
        size="small"
        extra={<Tag color="blue">一级</Tag>}
      >
        <StockTable data={displayStocks} onChange={onTableChange} sortBy={sort.sortBy} sortOrder={sort.sortOrder === "asc" ? "ascend" : "descend"} loading={stocksLoading} />
      </Card>
    </Space>
  );
}
