/**
 * 占位模块的**路线图**数据（Phase 4 §5.7）。
 *
 * ## 为什么要有这个文件（而不是在页面里写死几行文案）
 *
 * 8 个占位路由（`check-routes.mjs` 的 `PLACEHOLDER_CONTRACT`）原先只渲染一句
 * 「X 模块尚未落地，页面结构已就位」——那句话**不携带任何信息**：用户既不知道这个模块
 * 会有哪些子模块，也不知道它卡在什么数据上。占位页本该回答的是"**这个模块需要什么数据**"。
 *
 * 本文件把 IA 文档 §2（各导航的子模块树，逐条转录 plan §3.3）里**已经拍过板**的子模块
 * 落成数据，于是：
 * - 渲染层（`PlaceholderNotice`）只负责排版，不持有业务知识；
 * - **原产地唯一**：子模块名逐字取自 IA §2，不在这里新造概念；
 * - 可机械校验：`e2e/placeholder.spec.ts` 断言 8 条占位路由**每条 ≥3 个 roadmap 项**
 *   （判据的来源是 `PLACEHOLDER_CONTRACT`，不是本文件的键集合——否则本文件漏一个模块，
 *   测试会连"该测谁"都不知道，正是 §2.1 那类假绿的形态）。
 *
 * ## 与 `MODULE_TREES`（`route-manifest.ts`）的分工
 *
 * `MODULE_TREES` 是**侧栏导航契约**（每个节点必须给出一个可去的 `path`，且不得造未登记
 * 的真路由）；本文件是**占位页的路线图**（节点带 `status` 与"缺什么数据"，**不可点**）。
 * 两者同源（IA §2）但消费者不同，故不合并：把路线图塞进 `MODULE_TREES` 会让侧栏出现
 * 指向不存在子路由的链接——那正是 W1 明确避开的"假路由"。
 */

/** 子模块状态：只有两档，且**必须**由真实进度决定（不得写"即将上线"这种无判据的词） */
export type RoadmapStatus = "规划中" | "接入中";

export interface RoadmapItem {
  /** 子模块名，**逐字取自 IA 文档 §2** */
  name: string;
  status: RoadmapStatus;
  /** 该子模块卡在什么数据/能力上（一句话，写给用户看，不是写给开发看的） */
  needs: string;
}

export interface ModuleRoadmap {
  /** 模块名，逐字取自 IA 文档 §1（与 `NAV_ROOTS` 的 title 一致） */
  title: string;
  /** 该模块需要的数据总览（占位页顶部那句「这个模块需要什么数据」） */
  requires: string;
  /** 3–5 个子模块（IA §2 全量；超过 5 个的模块截前 5 个，见各条注释） */
  items: RoadmapItem[];
}

/**
 * 键 = **路由路径**（与 `PLACEHOLDER_CONTRACT` 同形），不是模块名——
 * 这样占位页可以按 `route.path` 直接取，不需要再做"路径 → 模块名"的映射表。
 */
export const MODULE_ROADMAP: Record<string, ModuleRoadmap> = {
  "/company": {
    title: "公司分析",
    requires: "定量数据（行情 / 财务 / 估值）已有管道；资讯类卡片依赖文本抽取",
    // IA §2.4 的 9 张卡；占位页只列前 5 张（把 9 张全铺开会把"路线图"变成第二份 README）
    items: [
      { name: "基本信息", status: "接入中", needs: "行情与名录已就绪，待与公司主表对齐口径" },
      { name: "风险红线", status: "接入中", needs: "ST / 退市 / 违规等风险标签的规则来源" },
      { name: "财务数据", status: "接入中", needs: "财报三表的结构化字段与报告期口径" },
      { name: "估值", status: "规划中", needs: "估值分位需要长期历史序列与同行业可比集" },
      { name: "事件驱动", status: "规划中", needs: "公告与研报的文本抽取（归资讯模块）" },
    ],
  },
  "/news": {
    title: "资讯",
    requires: "全产品文本原料的上游：公告 / 官媒 / 研报 / 媒体的采集与去重",
    items: [
      { name: "上市公司公告", status: "接入中", needs: "交易所公告源的正文抓取与去重" },
      { name: "政府官媒政策", status: "规划中", needs: "官媒发布源的分类与政策实体识别" },
      { name: "研究资讯", status: "规划中", needs: "卖方研报的合规获取路径" },
      { name: "市场资讯", status: "规划中", needs: "媒体源的去重与时效性判定" },
    ],
  },
  "/macro": {
    title: "宏观分析",
    requires: "宏观数据（统计局 / 央行口径）+ 政策文本，两者都要按发布日做 PIT 对齐",
    items: [
      { name: "顶层政策", status: "规划中", needs: "政策文本源与政策级别的分类口径" },
      { name: "货币政策", status: "规划中", needs: "利率 / 存准 / 公开市场操作的日频序列" },
      { name: "财政政策", status: "规划中", needs: "财政收支与专项债的月度序列" },
      { name: "产业政策", status: "规划中", needs: "行业级政策事件的实体识别" },
      { name: "政策影响追踪", status: "规划中", needs: "政策事件与行业/个股的关联规则" },
    ],
  },
  "/valuegraph": {
    title: "价值网图谱",
    requires: "分类（已有）+ 公告 / 研报抽取 + 工商数据；图谱的边来自文本抽取",
    items: [
      { name: "供应 / 产业链", status: "规划中", needs: "上中下游关系与传导时滞的抽取来源" },
      { name: "上市公司控股关系", status: "规划中", needs: "工商股权数据与上市公司的实体对齐" },
      { name: "产业链地图", status: "规划中", needs: "利润流向与替代/互补品的标注数据" },
    ],
  },
  "/strategy": {
    title: "策略引擎",
    requires: "消费上面全部模块的产出；本模块自身是规则引擎，不需要新增外部数据源",
    items: [
      { name: "选股", status: "规划中", needs: "多因子所需的财务与行情字段已就绪，待因子口径定稿" },
      { name: "择时", status: "规划中", needs: "技术 / 宏观 / 情绪三类判据的合成规则" },
      { name: "信号系统", status: "规划中", needs: "置信度分级 → 仓位的映射参数" },
      { name: "组合构建", status: "规划中", needs: "约束条件（行业上限 / 单票上限）的配置来源" },
      { name: "策略回测", status: "规划中", needs: "无未来函数的 PIT 数据快照能力" },
    ],
  },
  "/risk": {
    title: "风险管理",
    requires: "持仓数据来自「交易执行」；风险指标另需情绪 / 宏观 / 资讯的输入",
    items: [
      { name: "个股风控", status: "规划中", needs: "个股级阈值（回撤 / 波动 / 流动性）的参数表" },
      { name: "组合风控", status: "规划中", needs: "VaR / CVaR / Beta 暴露所需的持仓与行情面板" },
      { name: "系统性风险", status: "规划中", needs: "大盘信号、流动性指标与预案模板" },
      { name: "风控规则引擎", status: "规划中", needs: "自动止损 / 仓位调整的执行链路与审计日志" },
    ],
  },
  "/trade": {
    title: "交易执行",
    requires: "行情（已有）+ 行业分析的筛选结论；交易日志与持仓为人工录入 + 自动对账",
    items: [
      { name: "关注池", status: "规划中", needs: "行业分析筛出的长期追踪标的池（依赖行业分析落地）" },
      { name: "资产类型", status: "规划中", needs: "标的分类口径（由原图「标的分类」拆出）" },
      { name: "交易日志", status: "规划中", needs: "成交回报的录入与盈亏归因规则" },
      { name: "持仓管理", status: "规划中", needs: "持仓快照与成本口径（含分红送转的复权处理）" },
    ],
  },
  "/search": {
    title: "搜索",
    requires: "跨股票 / 行业 / 指数 / 概念 / 资讯的统一检索面；可检索范围取决于上述模块的落地进度",
    items: [
      { name: "股票与指数", status: "接入中", needs: "名录已就绪，待接入相关性排序" },
      { name: "申万行业", status: "接入中", needs: "三级行业树已就绪，待与检索词做别名对齐" },
      { name: "概念板块", status: "接入中", needs: "概念成分表已落库，待补概念别名与热度排序" },
      { name: "资讯全文", status: "规划中", needs: "全文检索依赖资讯模块的正文入库（见「资讯」）" },
    ],
  },
};

/** 取某条占位路由的路线图；未登记返回 `undefined`（调用方负责降级，不静默编数据） */
export function roadmapOf(path: string): ModuleRoadmap | undefined {
  return MODULE_ROADMAP[path];
}
