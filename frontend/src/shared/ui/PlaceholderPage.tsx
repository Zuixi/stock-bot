import { useLocation, useParams, useSearchParams } from "react-router-dom";
import { StateWrapper, SectionCard } from "@/shared/ui";
import { PlaceholderNotice } from "./PlaceholderNotice";

/**
 * 「规划中」占位页（IA 文档 §5.2 的 no-404 契约）。
 *
 * 只承诺三件事：**可达 + 不 404 + 规划中提示**——不承诺数据。
 * 模块真页面落地时替换本组件即可，**路由形状不变**。
 *
 * **参数保留**：占位页必须把 query 参数与路径参数原样显示出来，证明 redirect 没吞参数
 * （如带 `?q=` / `?tab=` 的访问）。
 *
 * 通告条本体是 `PlaceholderNotice`（带 `data-testid="placeholder-route-params"`）；
 * 路由清单也会把它注入到"页面自身不渲染占位"的占位路由上（如 `/company` 的占位通告由路由注入）。
 */
export interface PlaceholderPageProps {
  /** 模块名（一级导航名，逐字取自 IA 文档 §1） */
  title: string;
  /** 该模块的一行说明；缺省用通用措辞 */
  hint?: string;
  /** 额外要展示的 query 参数名（占位页会自动展示全部 query，此项仅用于强调） */
  highlightQueryKeys?: string[];
}

export function PlaceholderPage({ title, hint, highlightQueryKeys }: PlaceholderPageProps) {
  const location = useLocation();
  const params = useParams();
  const [searchParams] = useSearchParams();

  const paramEntries = Object.entries(params).filter(([, v]) => v !== undefined && v !== "");
  const queryEntries = [...searchParams.entries()];

  return (
    <SectionCard id={`placeholder-${title}`} title={title}>
      <StateWrapper empty emptyText={<PlaceholderNotice title={title} hint={hint} />}>
        {null}
      </StateWrapper>

      {/* 参数保留自证：占位路由不得把带参访问收敛成无参空页 */}
      <div
        style={{ marginTop: 12, fontSize: 12, color: "var(--text-secondary)", textAlign: "center" }}
      >
        <div>
          当前路径：<code data-testid="placeholder-pathname">{location.pathname}</code>
        </div>
        {paramEntries.length > 0 && (
          <div>
            路径参数：
            {paramEntries.map(([k, v]) => (
              <code key={k} style={{ marginInlineStart: 6 }}>
                {k}={v}
              </code>
            ))}
          </div>
        )}
        {queryEntries.length > 0 && (
          <div>
            查询参数：
            {queryEntries.map(([k, v]) => (
              <code
                key={k}
                style={{ marginInlineStart: 6 }}
                data-highlighted={highlightQueryKeys?.includes(k) ? "true" : undefined}
              >
                {k}={v}
              </code>
            ))}
          </div>
        )}
      </div>
    </SectionCard>
  );
}
