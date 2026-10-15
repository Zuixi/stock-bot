import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Alert, Radio, Select, Space, Spin, Tag, Tooltip, Typography } from "antd";
import { useNavigate, useSearchParams } from "react-router-dom";
import { SectionCard, FreshnessNote } from "@/shared/ui";
import {
  fetchHeatmapConceptBoards,
  fetchHeatmapSwL1,
  fetchHeatmapTreemap,
} from "@/shared/api/heatmap";
import { useWatchlist } from "@/features/watchlist/useWatchlist";
import { HeatmapTreemap } from "@/features/market/HeatmapTreemap";
import {
  HEATMAP_AREA_OPTIONS,
  HEATMAP_GROUPS,
  filterWatchlist,
  type HeatmapAreaKey,
  type HeatmapGroupKey,
} from "@/features/market/heatmapTree";
import { useTheme } from "@/app/theme-context";

/**
 * `/market/heatmap` 真实现（S1.5）——替换 Task 5 的占位页。
 *
 * - 数据：`GET /api/v1/market/heatmap/treemap` 一次性批量读路（≈5500 标的），
 *   四个分组维度共用同一批 items，分组在前端完成；
 * - 面积 = 市值 / 成交额（用户可切换，单位由后端下发、UI 显式标注）；
 * - 颜色 = 涨跌幅（红涨绿跌，语义固定，见 HeatmapTreemap 的色阶）；
 * - 「自选」走既有 watchlist（`useWatchlist`），不建平行自选；
 * - 公开免登录（ADR 0008）——本页不包 RequireAuth。
 *
 * 兼容保留：占位期入口带过 `?group=` / `?concept=` 参数（占位页如实回显二者），
 * 本页消费 `group`（`sw_l1|concept|all|watchlist`）并把 `concept` 码同步进板块
 * 选择；未知参数值回落 `all` 并如实提示，不静默吞掉。
 */
export default function MarketHeatmapPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const navigate = useNavigate();
  const { colors } = useTheme();

  const rawGroup = searchParams.get("group");
  const groupParam = (rawGroup ?? "all") as HeatmapGroupKey;
  const group: HeatmapGroupKey = HEATMAP_GROUPS.some((g) => g.key === groupParam)
    ? groupParam
    : "all";
  const conceptParam = searchParams.get("concept") ?? "";

  const [area, setArea] = useState<HeatmapAreaKey>("total_mv");

  const treemap = useQuery({
    queryKey: ["market", "heatmap", "treemap"],
    queryFn: fetchHeatmapTreemap,
    staleTime: 5 * 60 * 1000,
  });
  const swL1 = useQuery({
    queryKey: ["market", "heatmap", "sw-l1"],
    queryFn: fetchHeatmapSwL1,
    staleTime: 24 * 60 * 60 * 1000,
  });
  const concepts = useQuery({
    queryKey: ["market", "heatmap", "concept-boards"],
    queryFn: fetchHeatmapConceptBoards,
    staleTime: 24 * 60 * 60 * 1000,
  });

  const { items: watchlistSymbols } = useWatchlist();
  const watchlistSet = useMemo(() => new Set(watchlistSymbols), [watchlistSymbols]);

  const swNames = useMemo(
    () => new Map((swL1.data ?? []).map((r) => [r.code, r.name])),
    [swL1.data]
  );
  const conceptNames = useMemo(
    () => new Map((concepts.data ?? []).map((r) => [r.code, r.name])),
    [concepts.data]
  );

  const allItems = treemap.data?.items ?? [];
  const items = useMemo(
    () => (group === "watchlist" ? filterWatchlist(allItems, watchlistSet) : allItems),
    [allItems, group, watchlistSet]
  );

  const [agg, setAgg] = useState({ rendered: 0, aggregated: 0, groups: 0 });

  const setGroup = (next: HeatmapGroupKey) => {
    const params = new URLSearchParams(searchParams);
    params.set("group", next);
    if (next !== "concept") params.delete("concept");
    setSearchParams(params, { replace: true });
  };
  const setConcept = (code: string) => {
    const params = new URLSearchParams(searchParams);
    params.set("group", "concept");
    if (code) params.set("concept", code);
    else params.delete("concept");
    setSearchParams(params, { replace: true });
  };

  const areaOpt = HEATMAP_AREA_OPTIONS.find((o) => o.key === area);
  const quality = treemap.data?.as_of_quality ?? "partial";

  // 概念维度：选中板块 → 该板块整树（行上 concept_code 等于所选码的子集）；
  // 未选板块 → 全部概念分组树。
  const scopedItems = useMemo(
    () =>
      group === "concept" && conceptParam
        ? items.filter((it) => it.concept_code === conceptParam)
        : items,
    [items, group, conceptParam]
  );

  return (
    <SectionCard
      id="market-heatmap"
      title="市场热力图"
      note={treemap.data ? `${treemap.data.count} 标的` : undefined}
    >
      <Space direction="vertical" size="small" style={{ width: "100%" }}>
        {/* SectionCard 无 extra 槽（标题栏只有「查看全部」链）：切换工具条放正文首行 */}
        <Space size="middle" wrap data-testid="heatmap-toolbar">
          <Radio.Group
            size="small"
            value={group}
            onChange={(e) => setGroup(e.target.value as HeatmapGroupKey)}
            optionType="button"
            buttonStyle="solid"
            options={HEATMAP_GROUPS.map((g) => ({ label: g.label, value: g.key }))}
            data-testid="heatmap-group-switch"
          />
          <Tooltip
            title={`面积 = ${areaOpt?.label}（单位 ${areaOpt?.unit}）；颜色 = 涨跌幅（红涨绿跌）`}
          >
            <Radio.Group
              size="small"
              value={area}
              onChange={(e) => setArea(e.target.value as HeatmapAreaKey)}
              optionType="button"
              data-testid="heatmap-area-switch"
            >
              {HEATMAP_AREA_OPTIONS.map((o) => (
                <Radio.Button key={o.key} value={o.key} data-testid={`heatmap-area-${o.key}`}>
                  面积: {o.label}
                </Radio.Button>
              ))}
            </Radio.Group>
          </Tooltip>
        </Space>
        <FreshnessNote asOf={treemap.data?.as_of ?? undefined} quality={quality} />
        {group === "concept" ? (
          <Space wrap>
            <Select
              showSearch
              allowClear
              placeholder="选择概念板块（不选 = 全部概念分组树）"
              style={{ minWidth: 240 }}
              value={conceptParam || undefined}
              onChange={(v) => setConcept(v ?? "")}
              options={(concepts.data ?? []).map((c) => ({ value: c.code, label: c.name }))}
              loading={concepts.isLoading}
              data-testid="heatmap-concept-select"
            />
            {conceptParam ? (
              <Tag data-testid="heatmap-concept-current">
                {conceptNames.get(conceptParam) ?? conceptParam}
              </Tag>
            ) : null}
          </Space>
        ) : null}
        {group === "watchlist" && watchlistSymbols.length === 0 ? (
          <Alert
            type="info"
            showIcon
            message="自选为空"
            description="在个股详情或行情列表把标的加入自选后，这里会显示它们的热力图。"
          />
        ) : null}
        {rawGroup && !HEATMAP_GROUPS.some((g) => g.key === groupParam) ? (
          <Alert
            type="warning"
            showIcon
            message={`未知分组参数 group=${rawGroup}（已回落「全市场」）`}
          />
        ) : null}
        <div
          style={{
            display: "flex",
            gap: 12,
            alignItems: "center",
            flexWrap: "wrap",
            fontSize: 12,
            color: colors.textSecondary,
          }}
          data-testid="heatmap-legend"
        >
          <span>
            面积 = <b>{areaOpt?.label}</b>（{areaOpt?.unit}，缺数标的已剔除）
          </span>
          <span>
            颜色 = <b>涨跌幅</b>
          </span>
          <span style={{ color: colors.up }}>■ 涨</span>
          <span style={{ color: colors.down }}>■ 跌</span>
          <span>颜色越深幅度越大（±5% 封顶）</span>
          <span data-testid="heatmap-count">
            {treemap.data ? `标的数 ${scopedItems.length}` : ""}
          </span>
          {/* 聚合口径如实标注（spec §S1.5 性能契约的代价必须可见、可下钻） */}
          {agg.aggregated > 0 ? (
            <Tooltip
              title={`为满足首屏渲染性能，占比过小的矩形已合并为各分组的「其他 N 只」块。聚合块颜色 = 按面积加权的平均涨跌幅；点击聚合块可展开该组全部标的。`}
            >
              <span data-testid="heatmap-aggregate-note">
                已聚合 {agg.aggregated} 只 → {agg.groups} 个「其他」块（画出 {agg.rendered} 块，
                点击可展开）
              </span>
            </Tooltip>
          ) : null}
        </div>
        {treemap.isLoading ? (
          <div style={{ textAlign: "center", padding: 80 }}>
            <Spin size="large" />
          </div>
        ) : treemap.isError ? (
          <Alert
            type="error"
            showIcon
            message="热力图数据加载失败"
            description={String(treemap.error)}
          />
        ) : (
          <HeatmapTreemap
            items={scopedItems}
            group={group}
            area={area}
            swNames={swNames}
            conceptNames={conceptNames}
            onAggregated={setAgg}
            onSymbolClick={(symbol) => navigate(`/company/${symbol}`)}
          />
        )}
        <Typography.Paragraph type="secondary" style={{ marginBottom: 0, fontSize: 12 }}>
          口径：行情与板块聚合为 T+1 判据日（见上方 as_of 徽标）；市值取当日
          daily_basic，缺日回看最近一条、仍缺不显示；成交额/市值单位由后端下发
          （千元/万元）。数据一次性批量返回（当前 {treemap.data?.count ?? "—"}{" "}
          标的），四个分组维度共用同一批行。
        </Typography.Paragraph>
      </Space>
    </SectionCard>
  );
}
