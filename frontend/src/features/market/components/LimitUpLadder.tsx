import { Empty, Flex, Tag, Typography } from "antd";
import type { LadderStock } from "@/shared/api/limitUp";

interface Props {
  echelons: Array<{ streak: number; label: string; stocks: LadderStock[] }>;
  /** 端点降级（如 partial_day）时渲染短占位文案，不展示不完整数据。 */
  degraded?: boolean;
}

/**
 * 连板梯队：按 `echelons` 渲染分档块，档内每只涨停股一个 Tag。
 * `{daysSpan}天{boardsInWindow}板` 仅在 `boardsInWindow !== streak` 时显示（避免冗余）；
 * `缺少 N 个交易日` 披露停牌/无行情；封板时间缺失渲染 `--`（本地路径恒 null）。
 */
export function LimitUpLadder({ echelons, degraded = false }: Props) {
  if (degraded) {
    return (
      <div className="sentiment-ladder">
        <Typography.Text
          type="secondary"
          style={{ display: "block", padding: "24px 0", textAlign: "center" }}
        >
          数据不完整，暂不展示梯队
        </Typography.Text>
      </div>
    );
  }

  if (echelons.length === 0) {
    return (
      <div className="sentiment-ladder">
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="当日无连板梯队" />
      </div>
    );
  }

  return (
    <div className="sentiment-ladder">
      {echelons.map((e) => (
        <div key={e.streak} style={{ marginBottom: 16 }}>
          <Typography.Text strong style={{ fontSize: 14 }}>
            {e.label}
          </Typography.Text>
          <Flex wrap gap={8} style={{ marginTop: 8 }}>
            {e.stocks.map((s) => (
              <Tag key={s.symbol} style={{ margin: 0 }}>
                <Typography.Text strong style={{ fontSize: 13 }}>
                  {s.name}
                </Typography.Text>
                {s.boardsInWindow !== s.streak ? (
                  <Typography.Text type="secondary" style={{ fontSize: 12, marginLeft: 6 }}>
                    {s.daysSpan}天{s.boardsInWindow}板
                  </Typography.Text>
                ) : null}
                {s.missingDays > 0 ? (
                  <Typography.Text type="warning" style={{ fontSize: 12, marginLeft: 6 }}>
                    缺少 {s.missingDays} 个交易日
                  </Typography.Text>
                ) : null}
                <Typography.Text type="secondary" style={{ fontSize: 12, marginLeft: 6 }}>
                  封板 {s.sealTime ?? "--"}
                </Typography.Text>
              </Tag>
            ))}
          </Flex>
        </div>
      ))}
    </div>
  );
}
