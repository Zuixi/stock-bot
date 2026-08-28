import { useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { Breadcrumb, Card, Empty, Space, Tag, Typography } from "antd";
import type { TableProps } from "antd";
import { useQuery } from "@tanstack/react-query";
import { StockTable } from "@/features/market/components/StockTable";
import type { StockRecord } from "@/shared/types";
import { fetchSwIndustryTree, fetchSwLevel2Stocks, fetchSwLevel3Stocks, fetchSwLevel2StocksEnriched, fetchSwLevel3StocksEnriched } from "@/shared/api/swIndustry";

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
  const [selectedLevel3Code, setSelectedLevel3Code] = useState<string | undefined>();
  const [sort, setSort] = useState<SortState>({ sortBy: "symbol", sortOrder: "asc" });

  const { data: tree = [], isLoading: treeLoading } = useQuery({
    queryKey: ["sw-industry-tree"],
    queryFn: fetchSwIndustryTree,
  });
  const level1 = useMemo(() => tree.find((node) => node.code === level1Code), [tree, level1Code]);
  const level2 = useMemo(
    () => level1?.children.find((node) => node.code === level2Code),
    [level1, level2Code]
  );

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
          { title: <a onClick={() => navigate("/market")}>市场</a> },
          { title: <a onClick={() => navigate(`/market/industry/${level1.code}`)}>{level1.name}</a> },
          { title: level2.name },
        ]}
      />

      <Card
        title={`${level2.name} · 三级行业`}
        size="small"
        extra={<Typography.Text type="secondary">点击三级标签切换板块个股</Typography.Text>}
      >
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
