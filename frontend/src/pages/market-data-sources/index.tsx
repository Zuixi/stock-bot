import { useQuery } from "@tanstack/react-query";
import { Alert, Space, Spin, Table, Tag, Tooltip, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import { SectionCard } from "@/shared/ui";
import { fetchDataFreshness, type CoverageDomain } from "@/shared/api/dataFreshness";
import { fetchNorthbound } from "@/shared/api/marketData";
import {
  NORTHBOUND_QUERY,
  NORTHBOUND_SOURCE,
  normalizeSourceStatus,
  sourceStatusColor,
  sourceStatusText,
} from "@/shared/config/dataSourceCaliber";
import "./dataSources.css";

/**
 * `/market/data-sources` —— 数据版图 / 数据来源页（Phase 1 §W2.1，owner 裁定 E）。
 *
 * **为什么单独一页**：那份「数据版图」原先钉在 `/market` 每个 Tab 的底部（约 800px × 11 行），
 * 5 个 Tab 各看一遍（`1efe65a` 已把它移出工作台，但也没给它新家）。工作台需要的是
 * 「数据覆盖到哪一天」，不是「我们有多少个域」——后者是**信任装置**，属于独立页。
 *
 * **为什么数据是实测而不是静态表**：被移走的那版矩阵是 11 行手写常量，与真实数据可用性
 * 长期漂移（北向上游停更后靠手改常量把该行标成「规划中」）。本页改为消费
 * `GET /api/v1/market/data-freshness` 的真实读数（`coverage[]` 逐域
 * `have_days` / `complete_days` / `trading_days` / `cover_pct`，由库内实况算出），
 * 于是"我们覆盖了什么"这句话永远与库内一致。
 *
 * 纪律（与后端 `CoverageOut` 的注释同源）：
 * - `cover_pct === null` = **分母为 0**（该域还没有交易日窗口），渲染 `--`，**绝不 `?? 0`**；
 * - `have_days` 与 `complete_days` **必须并排**（Ruling 13）：`sector_moneyflow` 只认
 *   `capture_kind='eod_full'` 的行为完整日，盘中 poll 行是 inflow-Top-100 子集 ⇒ 两个数不等
 *   正是要暴露的事实，合并成一个数就把这个差异藏了；
 * - `status !== "ok"`（`missing_table` / `missing_column`）= 该域压根没查成（迁移未应用），
 *   此时计数恒 0 —— 必须显著标红，不能让 0% 被读成"确实没数据"。
 *
 * 权限：`/market/**` 公开免登录（ADR 0008）。
 */
function fmtPct(v: number | null): string {
  return v == null ? "--" : `${v.toFixed(2)}%`;
}

function fmtDay(v: string | null): string {
  return v ?? "--";
}

/** 覆盖率徽标：`null`（分母为空）与 `ok` 必须可区分，故先判 null 再判 status。 */
function CoverBadge({ row }: { row: CoverageDomain }) {
  if (row.status !== "ok") {
    return (
      <Tag color="red" data-testid={`coverage-status-${row.domain}`}>
        {row.status === "missing_table" ? "缺表" : "缺列"}
      </Tag>
    );
  }
  if (row.cover_pct == null) {
    return (
      <Tag data-testid={`coverage-status-${row.domain}`}>分母为空</Tag>
    );
  }
  const color = row.cover_pct >= 99 ? "green" : row.cover_pct >= 50 ? "orange" : "red";
  return (
    <Tag color={color} data-testid={`coverage-status-${row.domain}`}>
      {fmtPct(row.cover_pct)}
    </Tag>
  );
}

const COLUMNS: ColumnsType<CoverageDomain> = [
  {
    title: "数据域",
    dataIndex: "domain",
    key: "domain",
    width: 180,
    render: (domain: string, row) => (
      <Space direction="vertical" size={0}>
        <Typography.Text strong>{domain}</Typography.Text>
        {/* 表名 / 日期列是运维定位用的：出问题时能直接拿去 psql 查，不必回看后端源码 */}
        <Typography.Text type="secondary" style={{ fontSize: 11 }}>
          {row.table} · {row.date_column}
        </Typography.Text>
      </Space>
    ),
  },
  {
    title: "覆盖率",
    dataIndex: "cover_pct",
    key: "cover_pct",
    width: 100,
    align: "right",
    sorter: (a, b) => (a.cover_pct ?? -1) - (b.cover_pct ?? -1),
    render: (_: number | null, row) => <CoverBadge row={row} />,
  },
  {
    title: "完整日 / 应有交易日",
    key: "days",
    width: 180,
    align: "right",
    render: (_, row) => (
      <Tooltip
        title={
          `完整日 = 该域判据认可的天数（其余域 == have_days）；` +
          `有任何行的交易日 have_days = ${row.have_days}。` +
          (row.missing_days.length
            ? `窗口内缺 ${row.missing_days.length} 个交易日：${row.missing_days.slice(0, 5).join("、")}` +
              (row.missing_days.length > 5 ? " …" : "")
            : "窗口内无缺日。")
        }
      >
        <span className="ds-num num">
          {row.complete_days} / {row.trading_days}
        </span>
      </Tooltip>
    ),
  },
  {
    title: "窗口",
    key: "window",
    width: 200,
    render: (_, row) => (
      <span className="ds-num num">
        {fmtDay(row.first_day)} ~ {fmtDay(row.last_day)}
      </span>
    ),
  },
  {
    title: "窗口内行数",
    dataIndex: "row_count",
    key: "row_count",
    width: 110,
    align: "right",
    sorter: (a, b) => a.row_count - b.row_count,
    render: (v: number) => <span className="ds-num num">{v.toLocaleString("zh-CN")}</span>,
  },
];

export default function MarketDataSourcesPage() {
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["market", "data-freshness"],
    queryFn: fetchDataFreshness,
    // 巡检读数按日变化：5 分钟 staleTime 足够，且避免翻页时反复打库
    staleTime: 5 * 60 * 1000,
  });

  const coverage = data?.coverage ?? [];
  const failed = data?.failed_jobs ?? [];
  const refreshable = Object.entries(data?.domains ?? {});
  const stale = refreshable.filter(([, d]) => d.status !== "ok");

  // 北向（§5.8）：与 `NorthboundCard` **同一个 query key + 同一个窗口天数** ⇒ 同一次请求、
  // 同一份缓存；状态也走同一个 `normalizeSourceStatus`（缺字段归 `discontinued`）。
  const { data: northbound } = useQuery({
    queryKey: NORTHBOUND_QUERY.key,
    queryFn: () => fetchNorthbound(NORTHBOUND_QUERY.days),
    staleTime: 5 * 60 * 1000,
  });
  const nbStatus = normalizeSourceStatus(northbound?.sourceStatus);

  return (
    <Space direction="vertical" size={16} style={{ width: "100%" }}>
      <SectionCard
        id="market-data-sources"
        title="数据版图 · 数据来源"
        asof={data?.expected_latest ?? undefined}
        quality={data?.degraded_calendar ? "partial" : "complete"}
        note={data ? `巡检日 ${data.as_of}` : undefined}
      >
        <Space direction="vertical" size={12} style={{ width: "100%" }}>
          <div className="ds-stats" data-testid="data-sources-stats">
            <div className="ds-stat">
              <div className="ds-stat__num num">{coverage.length}</div>
              <div className="ds-stat__label">只读巡检域</div>
            </div>
            <div className="ds-stat">
              <div className="ds-stat__num num">{refreshable.length}</div>
              <div className="ds-stat__label">可自愈对账域</div>
            </div>
            <div className="ds-stat">
              <div className="ds-stat__num num">
                {data ? data.universe.toLocaleString("zh-CN") : "--"}
              </div>
              <div className="ds-stat__label">股票名录（universe）</div>
            </div>
            <div className="ds-stat">
              <div className="ds-stat__num num">{failed.length}</div>
              <div className="ds-stat__label">最近失败任务</div>
            </div>
          </div>

          {isLoading ? (
            <div style={{ textAlign: "center", padding: 60 }}>
              <Spin size="large" />
            </div>
          ) : isError ? (
            <Alert
              type="error"
              showIcon
              message="数据新鲜度巡检取不到"
              description={String(error)}
            />
          ) : (
            <>
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                逐域覆盖率（只读巡检；cover_pct 以<b>完整日</b>为分子、该域区间内的
                <b>交易日</b>为分母）。「分母为空」= 该域还没有任何交易日窗口，<b>不是 0% 覆盖</b>。
              </Typography.Text>
              {/* Phase 3 §4.2 的表格横向滚动容器约定：窄屏包 overflow-x，不把表格挤出视口 */}
              <div className="table-scroll">
                <Table<CoverageDomain>
                  size="small"
                  rowKey="domain"
                  columns={COLUMNS}
                  dataSource={coverage}
                  pagination={false}
                  scroll={{ x: "max-content" }}
                  data-testid="data-coverage-table"
                />
              </div>

              <Typography.Text strong style={{ marginTop: 8 }}>
                可自愈对账域（缺日会被对账器回补）
              </Typography.Text>
              <div className="ds-refreshable" data-testid="refreshable-domains">
                {refreshable.length === 0 ? (
                  <Typography.Text type="secondary">无</Typography.Text>
                ) : (
                  refreshable.map(([name, d]) => (
                    <Tooltip
                      key={name}
                      title={
                        (d.latest_in_db ? `库内最新 ${d.latest_in_db}；` : "库内无数据；") +
                        (d.missing_days.length
                          ? `缺 ${d.missing_days.join("、")}`
                          : "窗口内无缺日") +
                        (d.partial_days.length ? `；不完整日 ${d.partial_days.join("、")}` : "")
                      }
                    >
                      <Tag color={d.status === "ok" ? "green" : "orange"}>
                        {name} · {d.status}
                        {d.latest_in_db ? ` · ${d.latest_in_db}` : ""}
                      </Tag>
                    </Tooltip>
                  ))
                )}
              </div>
              {stale.length > 0 ? (
                <Alert
                  type="warning"
                  showIcon
                  data-testid="stale-domains-alert"
                  message={`${stale.length} 个可自愈域不是 ok：${stale
                    .map(([name, d]) => `${name}(${d.status})`)
                    .join("、")}`}
                />
              ) : null}
            </>
          )}

          {/* ── 北向资金（Phase 4 §5.8）──────────────────────────────────────
              这一块**不是**新增的第二份数据版图常量：`NORTHBOUND_SOURCE` 与工作台的
              `NorthboundCard` 用的是**同一个常量**（`shared/config/dataSourceCaliber.ts`），
              连 React Query 的 key 与窗口天数也共用 `NORTHBOUND_QUERY`（同一次请求、
              同一份缓存）——「一处常量、两处表面、不漂移」就是靠这两件事成立的。
              为什么它不是 `coverage[]` 里的一行：那张表的域集合由后端
              `GET /market/data-freshness` 的 `coverage[]` 决定（实测 8 个域，不含北向），
              前端**不得**往里塞一行本地数据（那就又回到被删掉的静态矩阵那条路）。 */}
          <div className="ds-northbound" data-testid="data-sources-northbound">
            <div className="ds-northbound__head">
              <Typography.Text strong style={{ fontSize: 13 }}>
                {NORTHBOUND_SOURCE.label}
              </Typography.Text>
              <Tag
                color={sourceStatusColor(NORTHBOUND_SOURCE, nbStatus)}
                style={{ fontSize: 11, lineHeight: "16px", padding: "0 6px", marginInlineEnd: 0 }}
                data-source-key={NORTHBOUND_SOURCE.key}
                data-source-status={nbStatus}
              >
                {sourceStatusText(NORTHBOUND_SOURCE, nbStatus)}
              </Tag>
            </div>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              {NORTHBOUND_SOURCE.scope}
              {northbound?.asOf ? (
                <>
                  ；库内最新 <span className="ds-num num">{northbound.asOf}</span>
                </>
              ) : (
                "；库内最新 --"
              )}
            </Typography.Text>
            <Typography.Text type="secondary" style={{ fontSize: 11, display: "block", marginTop: 2 }}>
              产地 <code>{NORTHBOUND_SOURCE.upstream}</code>（公开行情页不披露供应商名，
              本页是运维向的来源登记，见 e2e/marketTrust.spec.ts 的门禁范围）
            </Typography.Text>
          </div>

          {failed.length > 0 ? (
            <Alert
              type={failed.length > 5 ? "error" : "warning"}
              showIcon
              data-testid="failed-jobs-alert"
              message={`最近 ${failed.length} 个调度任务失败`}
              description={
                <ul className="ds-failed-list">
                  {failed.map((f) => (
                    <li key={`${f.job_id}-${f.at}`}>
                      <b>{f.job_id}</b>{" "}
                      <span className="ds-num num">{f.at.slice(0, 19).replace("T", " ")}</span>{" "}
                      <span className="ds-failed-error">{f.error}</span>
                    </li>
                  ))}
                </ul>
              }
            />
          ) : null}

          <Typography.Paragraph type="secondary" style={{ marginBottom: 0, fontSize: 12 }}>
            口径：本页数据来自后端只读巡检端点 <code>GET /api/v1/market/data-freshness</code>
            （<code>apply=false</code>，绝不触发回补）。行情/估值/涨跌停价三类主域的
            「数据截至」以工作台各卡片的 as_of 徽标为准；本页只回答「我们接了哪些域、每个域
            在窗口内完整到哪一天」。上游来源（TuShare / 东财 / 巨潮等）与逐域内容说明见
            <code>docs/design/landing-market-theme.md</code> 的「已下线」历史存档一节。
          </Typography.Paragraph>
        </Space>
      </SectionCard>
    </Space>
  );
}
