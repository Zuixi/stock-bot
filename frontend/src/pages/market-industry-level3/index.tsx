import { useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { Alert, Breadcrumb, Button, Card, Empty, Space, Tag, Typography } from "antd";
import type { TableProps } from "antd";
import { useQuery } from "@tanstack/react-query";
import { StockTable } from "@/features/market/components/StockTable";
import type { StockRecord } from "@/shared/types";
import { fetchSwIndustryTree, fetchSwLevel2Stocks, fetchSwLevel3Stocks, fetchSwLevel2StocksEnriched, fetchSwLevel3StocksEnriched } from "@/shared/api/swIndustry";
import { fetchIndustryLevel } from "@/shared/api/swDrilldown";
import {
  IndustryAsOfBar,
  IndustryMetricStrip,
} from "@/features/market/components/IndustryMetricStrip";
import { fetchIndustries } from "@/shared/api/industryResearch";
import { useAuthStore } from "@/features/auth";

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

export default function IndustryLevel3Page() {
  const navigate = useNavigate();
  const { level1Code = "", level2Code = "" } = useParams();
  const { hasPermission } = useAuthStore();
  const [selectedLevel3Code, setSelectedLevel3Code] = useState<string | undefined>();
  const [sort, setSort] = useState<SortState>({ sortBy: "symbol", sortOrder: "asc" });

  const { data: tree = [], isLoading: treeLoading } = useQuery({
    queryKey: ["market", "sw-industry-tree"],
    queryFn: fetchSwIndustryTree,
  });
  const level1 = useMemo(() => tree.find((node) => node.code === level1Code), [tree, level1Code]);
  const level2 = useMemo(
    () => level1?.children.find((node) => node.code === level2Code),
    [level1, level2Code]
  );

  // ── S1.4：该二级行业下各三级行业的 7 项聚合 + SCD2 归属口径 ──
  const { data: drill } = useQuery({
    queryKey: ["sw-drill-levels", 3, level1Code, level2Code],
    queryFn: () => fetchIndustryLevel(3, { level1Code, level2Code }),
    enabled: Boolean(level1Code && level2Code),
    staleTime: 60_000,
  });
  const metricsByCode = useMemo(
    () => new Map((drill?.items ?? []).map((it) => [it.code, it.metrics])),
    [drill?.items]
  );

  // ── 已产品化行业导航：当前二级（或选中三级）命中 registry sw_l3_codes 时出 banner ──
  // 复用 /research 列表页的 "industries" 查询缓存
  // 该接口需 research:read（docs/decisions/0008）：无权限者不发请求，banner 自然不渲染
  const { data: productizedIndustries = [] } = useQuery({
    queryKey: ["industries"],
    queryFn: fetchIndustries,
    enabled: hasPermission("research:read"),
    staleTime: 5 * 60 * 1000,
  });
  const workbenchIndustry = useMemo(() => {
    const codes = selectedLevel3Code
      ? [selectedLevel3Code]
      : (level2?.children.map((c) => c.code) ?? []);
    return productizedIndustries.find((ind) =>
      ind.swL3Codes.some((code) => codes.includes(code))
    );
  }, [productizedIndustries, selectedLevel3Code, level2]);

  // ── Fast: basic stock metadata (renders immediately) ──
  const { data: baseLevel2Stocks = [], isLoading: level2Loading } = useQuery({
    queryKey: ["sw-level2-stocks", level1Code, level2Code],
    queryFn: () => fetchSwLevel2Stocks(level1Code, level2Code),
    enabled: Boolean(level1Code && level2Code),
  });
  const { data: baseLevel3Stocks = [], isLoading: level3Loading } = useQuery({
    queryKey: ["sw-level3-stocks", level1Code, level2Code, selectedLevel3Code],
    queryFn: () => fetchSwLevel3Stocks(level1Code, level2Code, selectedLevel3Code ?? ""),
    enabled: Boolean(level1Code && level2Code && selectedLevel3Code),
  });

  // ── Deferred: quote + daily_basic (fills financial columns async) ──
  const { data: enrichedLevel2Stocks = [] } = useQuery({
    queryKey: ["sw-level2-stocks-enriched", level1Code, level2Code],
    queryFn: () => fetchSwLevel2StocksEnriched(level1Code, level2Code),
    enabled: Boolean(level1Code && level2Code && baseLevel2Stocks.length > 0),
    staleTime: 30_000,
    refetchOnWindowFocus: false,
  });
  const { data: enrichedLevel3Stocks = [] } = useQuery({
    queryKey: ["sw-level3-stocks-enriched", level1Code, level2Code, selectedLevel3Code],
    queryFn: () => fetchSwLevel3StocksEnriched(level1Code, level2Code, selectedLevel3Code ?? ""),
    enabled: Boolean(level1Code && level2Code && selectedLevel3Code && baseLevel3Stocks.length > 0),
    staleTime: 30_000,
    refetchOnWindowFocus: false,
  });

  const stocks = useMemo(() => {
    const baseSource = selectedLevel3Code ? baseLevel3Stocks : baseLevel2Stocks;
    const enrichedSource = selectedLevel3Code ? enrichedLevel3Stocks : enrichedLevel2Stocks;
    const enrichedMap = new Map(enrichedSource.map((s) => [s.symbol, s]));
    const merged = baseSource.map((s) => enrichedMap.get(s.symbol) ?? s);
    return applySort(merged, sort);
  }, [baseLevel2Stocks, baseLevel3Stocks, enrichedLevel2Stocks, enrichedLevel3Stocks, selectedLevel3Code, sort]);

  const onTableChange: TableProps<StockRecord>["onChange"] = (_pagination, _filters, sorter) => {
    if (!Array.isArray(sorter) && sorter.field) {
      setSort({
        sortBy: sorter.field as keyof StockRecord,
        sortOrder: sorter.order === "ascend" ? "asc" : "desc",
      });
    }
  };

  if (!level1 || !level2) {
    return (
      <Card>
        {treeLoading ? <Typography.Text type="secondary">行业数据加载中...</Typography.Text> : <Empty description="未找到对应的行业层级" />}
      </Card>
    );
  }

  return (
    <Space direction="vertical" size={16} style={{ width: "100%" }}>
      <Breadcrumb
        items={[
          { title: <Link to="/market">市场</Link> },
          {
            title: (
              <Link to={`/market/industry/${level1.code}`}>{level1.name}</Link>
            ),
          },
          { title: level2.name },
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
        <Card title={`${level2.name} · 7 项行情字段（二级聚合）`} size="small">
          <IndustryMetricStrip metrics={drill.parent.metrics} />
        </Card>
      ) : null}

      {workbenchIndustry && (
        <Card size="small">
          <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
            <Typography.Text>
              <b>{workbenchIndustry.name}</b>
              <span style={{ color: "#86909c", marginLeft: 8 }}>
                已产品化 — 周期信号 / 指标带 / 标的分析
              </span>
            </Typography.Text>
            <Button
              type="primary"
              size="small"
              onClick={() => navigate(`/industry/${workbenchIndustry.key}`)}
            >
              进入投研工作台
            </Button>
          </div>
        </Card>
      )}

      <Card
        title={`${level2.name} · 三级行业`}
        size="small"
        extra={<Typography.Text type="secondary">点击三级进入末级成分股页（含 7 项字段与 SCD2 归属）</Typography.Text>}
      >
        <Space direction="vertical" size={8} style={{ width: "100%" }}>
          <Space wrap>
            <Tag.CheckableTag
              checked={!selectedLevel3Code}
              onChange={(checked) => {
                if (checked) setSelectedLevel3Code(undefined);
              }}
            >
              查看二级全部
            </Tag.CheckableTag>
            {level2.children.map((level3) => (
              <Tag.CheckableTag
                key={level3.code}
                checked={selectedLevel3Code === level3.code}
                onChange={(checked) => {
                  if (checked) setSelectedLevel3Code(level3.code);
                }}
              >
                {level3.name} ({level3.stockCount})
              </Tag.CheckableTag>
            ))}
          </Space>
          {/* 末级入口：每条三级行业一行（7 项聚合 + 成分股链接）。
              用真 <Link> 而不是裸 onClick：键盘可达、可新开标签（Task 16 的既有纪律）。 */}
          <Space direction="vertical" size={4} style={{ width: "100%" }}>
            {level2.children.map((level3) => (
              <Space key={level3.code} size={12} wrap>
                <Link to={`/market/industry/${level1Code}/${level2Code}/${level3.code}`}>
                  {level3.name} · 成分股
                </Link>
                {metricsByCode.get(level3.code) ? (
                  <IndustryMetricStrip metrics={metricsByCode.get(level3.code) ?? null} compact />
                ) : null}
              </Space>
            ))}
          </Space>
        </Space>
      </Card>

      <Card
        title={`${selectedLevel3Code ? `${level2.children.find((i) => i.code === selectedLevel3Code)?.name ?? "三级行业"}` : level2.name} · 个股最新信息`}
        size="small"
        extra={<Tag color={selectedLevel3Code ? "purple" : "blue"}>{selectedLevel3Code ? "三级" : "二级"}</Tag>}
      >
        {stocks.length > 0 ? (
          <StockTable
            data={stocks}
            onChange={onTableChange}
            sortBy={sort.sortBy}
            sortOrder={sort.sortOrder === "asc" ? "ascend" : "descend"}
            loading={selectedLevel3Code ? level3Loading : level2Loading}
          />
        ) : (
          <Empty description="暂无个股数据" />
        )}
      </Card>
    </Space>
  );
}
