import { Card, Tabs } from "antd";
import { DragonTigerTable } from "./dataFace/DragonTigerTable";
import { BlockTradeTable } from "./dataFace/BlockTradeTable";
import { ShareFloatTable } from "./dataFace/ShareFloatTable";
import { RepurchaseTable } from "./dataFace/RepurchaseTable";
import { AnnouncementFeed } from "./dataFace/AnnouncementFeed";

export function MarketDataBoard() {
  return (
    <Card title="数据面" size="small">
      <Tabs
        defaultActiveKey="dragon-tiger"
        items={[
          { key: "dragon-tiger", label: "龙虎榜", children: <DragonTigerTable /> },
          { key: "block-trades", label: "大宗交易", children: <BlockTradeTable /> },
          { key: "share-floats", label: "解禁", children: <ShareFloatTable /> },
          { key: "repurchases", label: "回购", children: <RepurchaseTable /> },
          { key: "announcements", label: "公告快讯", children: <AnnouncementFeed /> },
        ]}
      />
    </Card>
  );
}
