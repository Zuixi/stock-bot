import { useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { Breadcrumb, Card, Empty, Space, Tag, Typography } from "antd";
import type { TableProps } from "antd";
import { StockTable } from "@/features/market/components/StockTable";
import type { StockRecord } from "@/shared/types";
import { getLevel1Node, getLevel2Node, getLevel2Stocks, getLevel3Stocks } from "@/shared/mocks/swIndustry";

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
  const [sort, setSort] = useState<SortState>({ sortBy: "marketCap", sortOrder: "desc" });

  const level1 = useMemo(() => getLevel1Node(level1Code), [level1Code]);
  const level2 = useMemo(() => getLevel2Node(level1Code, level2Code), [level1Code, level2Code]);

  const baseLevel2Stocks = useMemo(() => getLevel2Stocks(level1Code, level2Code), [level1Code, level2Code]);
  const baseLevel3Stocks = useMemo(() => {
    if (!selectedLevel3Code) return [];
    return getLevel3Stocks(level1Code, level2Code, selectedLevel3Code);
  }, [level1Code, level2Code, selectedLevel3Code]);

  const stocks = useMemo(() => {
    const source = selectedLevel3Code ? baseLevel3Stocks : baseLevel2Stocks;
    return applySort(source, sort);
  }, [baseLevel2Stocks, baseLevel3Stocks, selectedLevel3Code, sort]);

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
        <Empty description="未找到对应的行业层级" />
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
              {level3.name} ({getLevel3Stocks(level1.code, level2.code, level3.code).length})
            </Tag.CheckableTag>
          ))}
        </Space>
      </Card>

      <Card
        title={`${selectedLevel3Code ? `${level2.children.find((i) => i.code === selectedLevel3Code)?.name ?? "三级行业"}` : level2.name} · 个股最新信息`}
        size="small"
        extra={<Tag color={selectedLevel3Code ? "purple" : "blue"}>{selectedLevel3Code ? "三级" : "二级"}</Tag>}
      >
        {stocks.length > 0 ? <StockTable data={stocks} onChange={onTableChange} /> : <Empty description="暂无个股数据" />}
      </Card>
    </Space>
  );
}
