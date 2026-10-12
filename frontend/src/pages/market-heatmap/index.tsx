import { Alert, Typography } from "antd";
import { useLocation, useSearchParams } from "react-router-dom";
import { SectionCard, StateWrapper } from "@/shared/ui";

/**
 * `/market/heatmap`：热力图页（**今日不存在，本任务新建占位**）。
 *
 * 承接两条旧路由的 redirect（IA 文档 §4）：
 * - `/market/hot-sectors/:category` → `/market/heatmap?group=:category`
 * - `/market/concept/:boardCode`    → `/market/heatmap?concept=:boardCode`
 *
 * 两种 query 各要能渲染：本任务只**如实把收到的参数显示出来**（证明 redirect 未吞参数），
 * 真正的热力图实现留给市场模块自己的 spec。**不得静默忽略任何一个参数**。
 */
export default function MarketHeatmapPage() {
  const location = useLocation();
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

      <StateWrapper
        empty
        emptyText={
          <Typography.Text type="secondary">
            规划中：热力图渲染尚未落地（占位路由，无 404）
          </Typography.Text>
        }
      >
        {null}
      </StateWrapper>

      <div
        data-testid="placeholder-route-params"
        style={{ marginTop: 12, fontSize: 12, color: "var(--text-secondary)", textAlign: "center" }}
      >
        <div>
          当前路径：<code data-testid="placeholder-pathname">{location.pathname}</code>
        </div>
        {entries.length > 0 ? (
          <div>
            查询参数：
            {entries.map(([k, v]) => (
              <code key={k} style={{ marginInlineStart: 6 }}>
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
