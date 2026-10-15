import { useEffect, useRef } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Tabs, Typography } from "antd";
import {
  DatafaceSection,
  MoneyflowSection,
  OverviewSection,
  PanoramaSection,
  SentimentSection,
  sectionAnchor,
} from "./sections";
import "./market.css";

/**
 * 行情中心（P1-1 第二轮：页级 Tab 的**内容**改为五个独立分区**堆叠**，Tab 条降级为分区导航）。
 *
 * 对标（owner 批准的竞品形态）：行情页 = 一堆各自独立的分区，每个分区自带本地维度条与
 * 自己的口径，**一屏读到多个视角**，而不是把四个视角藏在页级 Tab 后面（一次只看一个）。
 *
 * ⚠️ **契约（不许破）**：`?tab=<key>` 仍是"当前分区"的唯一真相，导航仍是 antd `Tabs`
 * （`role="tab"` + `aria-selected` + `.ant-tabs-nav .ant-tabs-tab` 的 DOM 形态）。原因不是
 * 懒得改，而是**四个既有 spec 就钉在这套契约上**：
 * - `e2e/marketLayout.spec.ts` 按 `TABS` 逐个点 `[data-testid="market-tabs"]` 里的 `role=tab`，
 *   并从 `.ant-tabs-nav .ant-tabs-tab` 读 labels 核对副标题同源；
 * - `e2e/marketDataFace.spec.ts` / `e2e/marketTrends.spec.ts` 用 `getByRole("tab", {name})` 切分区；
 * - `e2e/limitUpSentiment.spec.ts` 全 8 例 `goto("/market")` + 点「短线情绪」+ 断 `aria-selected`；
 * - `e2e/responsive.spec.ts` 扫 `/market?tab={overview,panorama,moneyflow,dataface,sentiment}`。
 * 所以本轮**保留 Tab 机制本身**，只把它的**面板（children）搬空**：内容改为堆叠的分区，
 * Tab 只负责"高亮 + 滚到那个分区"。`?tab=` 因此仍是可分享/可回退的 URL 状态
 * （返回键回到原分区的既有契约不变）。
 *
 * 默认分区不写参数：保住既有 `navigate("/market")` 与 `toHaveURL(/\/market$/)` 的语义；
 * `replace` 避免每点一次就往返回栈塞一条。
 */
const SECTION_KEYS = ["overview", "panorama", "moneyflow", "dataface", "sentiment"] as const;
type SectionKey = (typeof SECTION_KEYS)[number];

/** 分区导航（label 与分区标题**分层**：导航是"去哪一块"，分区标题是"这块是什么"）。 */
const NAV: Array<{ key: SectionKey; label: string }> = [
  { key: "overview", label: "指数总览" },
  { key: "panorama", label: "A股全景" },
  { key: "moneyflow", label: "资金流向" },
  { key: "dataface", label: "数据面" },
  { key: "sentiment", label: "短线情绪" },
];

const isSectionKey = (value: string | null): value is SectionKey =>
  value != null && (SECTION_KEYS as readonly string[]).includes(value);

export default function MarketPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  // 认不出的 `?tab=`（手改 URL / 旧链接）一律落回默认分区，不渲染空白页
  const raw = searchParams.get("tab");
  const tab: SectionKey = isSectionKey(raw) ? raw : "overview";
  const setTab = (key: string) =>
    setSearchParams(key === "overview" ? {} : { tab: key }, { replace: true });

  // 分区导航的**动作语义**：切分区 = 滚到那个分区（内容本来就都在屏上）。
  // 首帧（深链 `?tab=sentiment`）直接就位；之后由用户点击触发的切换用平滑滚动，
  // 让"跳到哪一块"这件事在视觉上留下痕迹（瞬时跳变读不出来）。
  const firstRun = useRef(true);
  useEffect(() => {
    document
      .getElementById(sectionAnchor(tab))
      ?.scrollIntoView({ behavior: firstRun.current ? "auto" : "smooth", block: "start" });
    firstRun.current = false;
  }, [tab]);

  return (
    <div className="market-page">
      <div className="market-page-head">
        <Typography.Title level={4} style={{ margin: 0 }}>
          行情中心
        </Typography.Title>
        {/* Phase 1 §W2.3：副标题**由导航 labels 推导**，不再手写字面量。
            此前是「全球指数 · A股全景 · 资金流向 · 龙虎榜/大宗/解禁/回购」——与 Tab 各说各话
            （第一项 Tab 叫「指数总览」不叫「全球指数」，末项把三个 Tab 塞成一句）。
            同一个列表渲染两处时必须同源，否则加/删分区后副标题必然漂移。 */}
        <Typography.Text type="secondary" style={{ fontSize: 13 }}>
          {NAV.map((i) => i.label).join(" · ")}
        </Typography.Text>
        {/* Phase 1 §W2.1 的工作台补偿入口：数据版图整块迁出工作台后（它钉在每个 Tab 底部，
            5 个 Tab 各看一遍），这里留一行入口链到独立页 `/market/data-sources`。
            12 = 只读巡检域数（实测 8；取计划 §2.1 的 12 会让这行说谎，故按实测 8 写）。
            ⚠️ **刻意不写供应商名**：计划 §2.1 原稿是「数据覆盖 12 个域 · TuShare/东财 · 查看来源 →」，
            但 `e2e/marketTrust.spec.ts`「公开页不披露数据来源 › /market 整页不得出现供应商名称」
            机械断言 `/market` 正文不得出现 TuShare/东财/巨潮/AKShare —— 那是**既有的、被测试守住的
            产品口径**（数据源构成属竞争资产）。计划那句若照抄就会当场转红；两者冲突时以既有门禁为准，
            故这行去掉供应商名。来源明细仍在独立页（那里也不再按厂商署名，只给表名/域名的实测读数）。 */}
        <Link className="market-page-head__sources" to="/market/data-sources">
          数据覆盖 8 个域 · 查看来源 →
        </Link>
      </div>

      {/* 分区导航：`Tabs` 只留标签条（items 无 children ⇒ 不渲染任何面板），
          内容由下面的五个分区各自渲染。sticky 见 market.css（长页里导航一直可用）。 */}
      <Tabs
        className="market-page__nav"
        data-testid="market-tabs"
        activeKey={tab}
        onChange={setTab}
        items={NAV.map(({ key, label }) => ({ key, label }))}
      />

      <OverviewSection />
      <PanoramaSection />
      <MoneyflowSection />
      <DatafaceSection />
      <SentimentSection />
    </div>
  );
}
