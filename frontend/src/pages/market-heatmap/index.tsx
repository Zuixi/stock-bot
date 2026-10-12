import { Alert } from "antd";
import { useSearchParams } from "react-router-dom";
import { SectionCard } from "@/shared/ui";

/**
 * `/market/heatmap`：热力图页（**今日不存在，本任务新建占位**；真实现归 S1.5）。
 *
 * **它不是任何 redirect 的目标**（Ruling 30）：`/market/hot-sectors/:category` 与
 * `/market/concept/:boardCode` 今天就是有数据的真页面，故**原地保留**，不 redirect 到这里。
 *
 * 本页只承诺「可达 + 不 404 + 如实回显 query」——「规划中」通告由路由清单注入
 * （`route-manifest.ts` 的 `isPlaceholder: true` → `route-elements.tsx` 的 `PlaceholderNotice`），
 * 本文件**不再自带标记**，避免同一个 testid 在页面上出现两次。
 */
export default function MarketHeatmapPage() {
  const [searchParams] = useSearchParams();
  const group = searchParams.get("group");
  const concept = searchParams.get("concept");
  const entries = [...searchParams.entries()];

  return (
    <SectionCard id="market-heatmap" title="市场热力图">
      {group && !concept ? (
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 12 }}
          message={
            <span>
              板块口径：<b>group</b> = <code>{group}</code>
            </span>
          }
        />
      ) : null}
      {concept && !group ? (
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 12 }}
          message={
            <span>
              概念口径：<b>concept</b> = <code>{concept}</code>
            </span>
          }
        />
      ) : null}
      {group && concept ? (
        // 两个口径同时出现属于异常入口：显式暴露，不做"择一静默"处理
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 12 }}
          message="同时收到 group 与 concept 两个口径参数"
          description="该入口预期二者互斥；两个值均已原样保留，请核对来源链接。"
        />
      ) : null}

      {/* 参数保留自证：S1.5 接管时两种口径都要能渲染，且不得静默忽略参数 */}
      <div style={{ marginTop: 12, fontSize: 12, color: "var(--text-secondary)", textAlign: "center" }}>
        {entries.length > 0 ? (
          <div>
            查询参数：
            {entries.map(([k, v]) => (
              <code key={k} style={{ marginInlineStart: 6 }} data-testid={`heatmap-param-${k}`}>
                {k}={v}
              </code>
            ))}
          </div>
        ) : (
          <div>未收到任何查询参数（裸访问 /market/heatmap）</div>
        )}
      </div>
    </SectionCard>
  );
}
