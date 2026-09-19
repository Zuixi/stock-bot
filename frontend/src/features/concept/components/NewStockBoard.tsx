import { useMemo, useState, type ReactNode } from "react";
import { Alert, Card, Col, Empty, Row, Statistic, Table, Tag, Typography } from "antd";
import type { ColumnsType, TableProps } from "antd/es/table";
import { ChangeText, DegradedNotice, NumberText, formatCnDate } from "@/shared/ui";
import type { NewStockItem, NewStocksResponse } from "@/shared/api/concept";

interface Props {
  data?: NewStocksResponse;
  /** 端点降级：`degraded_reason` 非空时用共享 `DegradedNotice` 显示原因，不展示不完整数据。 */
  degraded?: boolean;
}

type SortKey = "listed_trade_days" | "pct_chg" | "turnover_rate" | "streak" | "amount";
type SortOrder = "ascend" | "descend";

/**
 * 客户端排序（列表量级小，分页在 Table 内部）：`sortOrder` 由表头点击驱动，
 * 行序与箭头同源。缺失值（渲染 `--`）**永远排最后**，不参与比较、也不许伪装成 0 抢排位。
 */
function compareRows(a: NewStockItem, b: NewStockItem, key: SortKey, order: SortOrder): number {
  const av = a[key];
  const bv = b[key];
  if (av == null && bv == null) return 0;
  if (av == null) return 1;
  if (bv == null) return -1;
  if (av === bv) return 0;
  return (av > bv ? 1 : -1) * (order === "ascend" ? 1 : -1);
}

function KpiTile({ title, children, note }: { title: string; children: ReactNode; note?: string }) {
  return (
    <Card size="small" style={{ height: "100%" }}>
      <Statistic title={title} valueRender={() => children} />
      {note ? (
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          {note}
        </Typography.Text>
      ) : null}
    </Card>
  );
}

/**
 * 「次新股情绪」卡（§3 触点 D）：KPI / 过滤 / 表格全部由**同一份 `items`** 聚合与渲染
 * （KPI 家数必须与表格行数同源，不新造口径）；`--` 是缺失，不是 0。
 * 注脚是口径契约不是装饰：BK0501 成分 + 「高于首日开盘」替代破发口径。
 */
export function NewStockBoard({ data, degraded = false }: Props) {
  const [onlyLimitUp, setOnlyLimitUp] = useState(false);
  const [sort, setSort] = useState<{ key: SortKey; order: SortOrder }>({
    key: "pct_chg",
    order: "descend",
  });

  const items = useMemo(() => data?.items ?? [], [data?.items]);

  const kpi = useMemo(() => {
    const pricedCount = items.filter((i) => i.pct_chg != null).length;
    const judged = items.filter((i) => i.never_broken != null);
    const aboveJudged = items.filter((i) => i.above_first_open != null);
    const sum = items.reduce((acc, i) => acc + (i.pct_chg ?? 0), 0);
    return {
      // 同一份 items 聚合，与表格行数同源
      limitUp: items.filter((i) => i.is_lu).length,
      /** 全部成员都不可判（限价缺失）→ `null`（渲染 `--`）；不可判 ≠ 0 家未开板。 */
      unbroken: judged.length === 0 ? null : items.filter((i) => i.never_broken === true).length,
      /** 可判家数（判据非 null 的行数）：部分可判时与瓦片数值一同上屏，缺失 ≠ 0。 */
      unbrokenJudged: judged.length,
      /** 同理：首日开盘价缺失的行不可判，不得计入「不高于开盘」也不得伪造成 0 家。 */
      aboveFirstOpen:
        aboveJudged.length === 0 ? null : items.filter((i) => i.above_first_open === true).length,
      aboveFirstOpenJudged: aboveJudged.length,
      avgPct: pricedCount === 0 ? null : sum / pricedCount,
      pricedCount,
    };
  }, [items]);

  const rows = useMemo(() => {
    const filtered = items.filter((i) => (onlyLimitUp ? (i.streak ?? 0) >= 1 : true));
    return [...filtered].sort((a, b) => compareRows(a, b, sort.key, sort.order));
  }, [items, onlyLimitUp, sort]);

  const onTableChange: TableProps<NewStockItem>["onChange"] = (_pagination, _filters, sorter) => {
    if (!Array.isArray(sorter) && sorter.field) {
      setSort({
        key: sorter.field as SortKey,
        order: sorter.order === "ascend" ? "ascend" : "descend",
      });
    }
  };

  if (degraded || data?.degraded_reason) {
    return (
      <div data-testid="new-stock-board">
        {data?.degraded_reason ? (
          <DegradedNotice reason={data.degraded_reason} />
        ) : (
          <Typography.Text
            type="secondary"
            style={{ display: "block", padding: "24px 0", textAlign: "center" }}
          >
            数据不完整，暂不展示次新股
          </Typography.Text>
        )}
      </div>
    );
  }

  if (items.length === 0) {
    return (
      <div data-testid="new-stock-board">
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="成分数据每日 18:20 刷新" />
      </div>
    );
  }

  const columns: ColumnsType<NewStockItem> = [
    { title: "名称", dataIndex: "name", width: 100 },
    {
      title: "上市日",
      dataIndex: "list_date",
      width: 96,
      render: (_, r) => (r.list_date ? formatCnDate(r.list_date) : "--"),
    },
    {
      title: "交易日数",
      dataIndex: "listed_trade_days",
      width: 88,
      align: "right",
      sorter: true,
      sortOrder: sort.key === "listed_trade_days" ? sort.order : undefined,
      render: (_, r) => (r.listed_trade_days == null ? "--" : r.listed_trade_days),
    },
    {
      title: "涨跌幅",
      dataIndex: "pct_chg",
      width: 90,
      align: "right",
      sorter: true,
      sortOrder: sort.key === "pct_chg" ? sort.order : undefined,
      render: (_, r) => <ChangeText value={r.pct_chg} />,
    },
    {
      title: "换手率",
      dataIndex: "turnover_rate",
      width: 90,
      align: "right",
      sorter: true,
      sortOrder: sort.key === "turnover_rate" ? sort.order : undefined,
      render: (_, r) => (r.turnover_rate == null ? "--" : `${r.turnover_rate.toFixed(2)}%`),
    },
    {
      title: "连板",
      dataIndex: "streak",
      width: 72,
      align: "right",
      sorter: true,
      sortOrder: sort.key === "streak" ? sort.order : undefined,
      // 不在梯队 → `--`（缺失 ≠ 0 板）
      render: (_, r) => (r.streak == null ? "--" : `${r.streak}板`),
    },
    {
      title: "成交额",
      dataIndex: "amount",
      width: 100,
      align: "right",
      sorter: true,
      sortOrder: sort.key === "amount" ? sort.order : undefined,
      // 后端 `amount` 沿 TuShare 口径为千元（与 `shared/api/stocks.ts:mapBackendStockEnriched` 同源），
      // 折算成元后交给 `unit="cap"` 分档，避免"218.12万"式的单位讹误。
      render: (_, r) => <NumberText value={r.amount == null ? null : r.amount * 1e3} unit="cap" />,
    },
  ];

  return (
    <div data-testid="new-stock-board">
      {/* I3：名录滞后（stock_id IS NULL）的成分不在 items/KPI 里——必须显式披露，
          否则每周六名录刷新前上市的新股会静默消失，KPI 与东财对不上且无从解释。 */}
      {data?.unresolved_count ? (
        <Alert
          type="warning"
          showIcon
          data-testid="new-stock-unresolved"
          style={{ marginBottom: 8 }}
          message={`另有 ${data.unresolved_count} 只成分股未收录（名录待刷新），未参与统计`}
        />
      ) : null}
      <Row gutter={[8, 8]}>
        <Col xs={12} md={6}>
          <KpiTile title="次新涨停" note="口径同连板梯队">
            <span>
              {kpi.limitUp}
              <span style={{ fontSize: 14, marginLeft: 4 }}>家</span>
            </span>
          </KpiTile>
        </Col>
        <Col xs={12} md={6}>
          {/* 部分可判（有行限价缺失）时必须带出可判家数：否则瓦片数值会把「不可判」静默算作「未开板」 */}
          <KpiTile
            title="未开板"
            note={`限价缺失不可判 → --${
              kpi.unbrokenJudged < items.length ? ` · n=${kpi.unbrokenJudged} 可判家数` : ""
            }`}
          >
            {kpi.unbroken == null ? (
              <span>--</span>
            ) : (
              <span>
                {kpi.unbroken}
                <span style={{ fontSize: 14, marginLeft: 4 }}>家</span>
              </span>
            )}
          </KpiTile>
        </Col>
        <Col xs={12} md={6}>
          <KpiTile title="平均涨跌幅" note={`n=${kpi.pricedCount} 有行情家数`}>
            <ChangeText value={kpi.avgPct} />
          </KpiTile>
        </Col>
        <Col xs={12} md={6}>
          {/* 同未开板：首日开盘价也缺失过，部分可判时带出可判家数 */}
          <KpiTile
            title="现价高于首日开盘"
            note={`替代破发口径${
              kpi.aboveFirstOpenJudged < items.length ? ` · n=${kpi.aboveFirstOpenJudged} 可判家数` : ""
            }`}
          >
            {kpi.aboveFirstOpen == null ? (
              <span>--</span>
            ) : (
              <span>
                {kpi.aboveFirstOpen}
                <span style={{ fontSize: 14, marginLeft: 4 }}>家</span>
              </span>
            )}
          </KpiTile>
        </Col>
      </Row>

      <div style={{ display: "flex", alignItems: "center", gap: 8, margin: "12px 0 8px" }}>
        {/* 过滤条件：`(streak ?? 0) >= 1`（不在梯队 = 未涨停，缺失 ≠ 0 板） */}
        <Tag.CheckableTag checked={onlyLimitUp} onChange={setOnlyLimitUp}>
          仅看连板
        </Tag.CheckableTag>
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          {rows.length} / {items.length} 家
        </Typography.Text>
      </div>

      <Table<NewStockItem>
        rowKey="symbol"
        size="small"
        columns={columns}
        dataSource={rows}
        pagination={{ pageSize: 20, size: "small", hideOnSinglePage: true, showSizeChanger: false }}
        scroll={{ x: 700 }}
        locale={{ emptyText: onlyLimitUp ? "当前筛选下无连板次新股" : "无次新股样本" }}
        onChange={onTableChange}
      />

      <div style={{ marginTop: 8 }}>
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          次新股 = 东财概念板块 BK0501 成分（上市 ≤1 年滚动）；「高于首日开盘」为替代口径，非破发
          （发行价未采集）；成分每日 18:20 刷新
        </Typography.Text>
        <br />
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          平均涨跌幅为成分均值（n={kpi.pricedCount} 有行情家数），非东财板块涨跌幅
          {data?.membership_as_of ? ` · 成分截至 ${formatCnDate(data.membership_as_of)}（东财）` : ""}
        </Typography.Text>
      </div>
    </div>
  );
}
