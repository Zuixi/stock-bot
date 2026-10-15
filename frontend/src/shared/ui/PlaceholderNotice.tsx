import { Tag, Typography } from "antd";
import { useLocation, useParams, useSearchParams } from "react-router-dom";
import { roadmapOf, type ModuleRoadmap, type RoadmapStatus } from "@/shared/config/module-roadmap";
import "./PlaceholderNotice.css";

/**
 * 「规划中」通告 + **模块路线图**（占位路由的统一标记）。
 *
 * **由路由清单注入**（见 `app/router/route-elements.tsx` 的 `PLACEHOLDER_NOTICE`，数据源
 * `app/router/route-manifest.ts`）：某条路由在 `ROUTES` 里
 * 标了 `isPlaceholder: true`，它的页面就一定会渲染本组件——判定不再依赖"页面文件里有没有某个 import"
 * 或"页面自己的文件里有没有某个字符串"（Ruling 33：那两种推断都出过假绿）。
 *
 * `data-testid="placeholder-route-params"` 是**对外可见的行为标记**：运行时验证（Playwright）
 * 与人工核对都以它为准。
 *
 * ## Phase 4 §5.7：从"一句话"升级为"路线图"
 *
 * 原实现只有 `规划中` + 一行 `X 模块尚未落地，页面结构已就位` —— 那句话对用户零信息量。
 * 现在同屏给出三件事（数据源 `shared/config/module-roadmap.ts`，逐条转录 IA 文档 §2）：
 * 1. **这个模块需要什么数据**（一行总览）；
 * 2. **3–5 个子模块**的标签化骨架（名称 + 状态徽章 `规划中 / 接入中`）；
 * 3. **每个子模块卡在什么数据上**（每个骨架项的 `needs` 一句话）。
 *
 * ⚠️ 路线图项**不是链接**：它们对应的真子路由尚未登记在 `route-manifest.ts` 里，
 * 做成链接就是造"假路由"（`check-routes.mjs` 会当场判 `表外路由`）。等 Wave 落地、
 * 真子路由登记后，再由那一批任务把 `needs` 换成 `path`。
 *
 * ⚠️ 未在 `MODULE_ROADMAP` 里登记的路径**回落到原通告文案**——不静默编一份假路线图
 * （"编数据"比"少一个特性"更坏）。
 */
export function PlaceholderNotice({
  title,
  hint,
  path,
}: {
  /** 模块名（逐字取自 IA 文档 §1） */
  title: string;
  /** 该模块的一行说明；缺省用通用措辞 */
  hint?: string;
  /** 路由路径（用于取路线图）；省略或未登记时只渲染通告条 */
  path?: string;
}) {
  const roadmap: ModuleRoadmap | undefined = path ? roadmapOf(path) : undefined;

  return (
    <div data-testid="placeholder-route-params" style={{ textAlign: "left" }}>
      <div style={{ textAlign: "center" }}>
        <Typography.Text strong>规划中</Typography.Text>
        <div>
          <Typography.Text type="secondary">
            {hint ?? `${title}模块尚未落地，页面结构已就位（本页为占位路由，无 404）。`}
          </Typography.Text>
        </div>
      </div>
      {roadmap ? <RoadmapSkeleton roadmap={roadmap} /> : null}
      <ParamSelfProof />
    </div>
  );
}

/**
 * **参数保留自证**（IA 文档 §5.2 的 no-404 契约，Phase 4 §5.7 补做）。
 *
 * 契约原文要求占位页"把 query 参数与路径参数原样显示出来，证明 redirect 没吞参数"。
 * 该能力原先只在 `shared/ui/PlaceholderPage`（带 `SectionCard` 的完整占位页，只有 `/me`
 * 用它）里实现，而 8 条占位路由走的是**本通告条**——于是契约对它们**并未兑现**
 * （实测：`/search?q=生猪` 的页面文本里没有 `q=生猪`，`placeholder-pathname` 也找不到）。
 * 这里把同一件事补到占位路由自己的渲染路径上，`data-testid` 沿用既有的两个名字，
 * 故 `PlaceholderPage` 那条路径的行为不变。
 */
function ParamSelfProof() {
  const location = useLocation();
  const params = useParams();
  const [searchParams] = useSearchParams();
  const queryEntries = [...searchParams.entries()];
  const paramEntries = Object.entries(params).filter(([, v]) => v !== undefined && v !== "");
  if (queryEntries.length === 0 && paramEntries.length === 0) return null;

  return (
    <div className="placeholder-params">
      当前路径：
      <code data-testid="placeholder-pathname">{location.pathname}</code>
      {paramEntries.length > 0 ? (
        <>
          {" "}
          路径参数：
          {paramEntries.map(([k, v]) => (
            <code key={k} style={{ marginInlineStart: 6 }}>
              {k}={v}
            </code>
          ))}
        </>
      ) : null}
      {queryEntries.length > 0 ? (
        <>
          {" "}
          查询参数：
          {queryEntries.map(([k, v]) => (
            <code key={k} style={{ marginInlineStart: 6 }}>
              {k}={v}
            </code>
          ))}
        </>
      ) : null}
    </div>
  );
}

const STATUS_COLOR: Record<RoadmapStatus, string> = {
  接入中: "gold",
  规划中: "default",
};

/** 路线图骨架：一句"需要什么数据" + 子模块标签墙（每项带状态与缺什么） */
function RoadmapSkeleton({ roadmap }: { roadmap: ModuleRoadmap }) {
  return (
    <div className="placeholder-roadmap" data-testid="placeholder-roadmap">
      <div className="placeholder-roadmap__requires">
        <span className="placeholder-roadmap__requires-label">这个模块需要什么数据</span>
        <span>{roadmap.requires}</span>
      </div>
      <ul className="placeholder-roadmap__grid" data-roadmap-count={roadmap.items.length}>
        {roadmap.items.map((item) => (
          <li
            key={item.name}
            className="placeholder-roadmap__item"
            data-roadmap-item={item.name}
            data-roadmap-status={item.status}
          >
            <div className="placeholder-roadmap__head">
              <span className="placeholder-roadmap__name">{item.name}</span>
              <Tag
                color={STATUS_COLOR[item.status]}
                style={{ marginInlineEnd: 0, fontSize: 11, lineHeight: "16px", padding: "0 6px" }}
              >
                {item.status}
              </Tag>
            </div>
            <div className="placeholder-roadmap__needs num">{item.needs}</div>
          </li>
        ))}
      </ul>
      <div className="placeholder-roadmap__foot">
        图例：<b>接入中</b> = 数据管道已存在、正在对齐口径；<b>规划中</b> = 依赖上游模块先说，
        <b>不作为任何时间承诺</b>（本页是路线图骨架，不是排期表）。
      </div>
    </div>
  );
}
