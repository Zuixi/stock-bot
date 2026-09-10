import { Tabs } from "antd";
import { Link } from "react-router-dom";
import { AnnouncementFeed } from "./dataFace/AnnouncementFeed";
import "./MarketNewsFeed.css";

const TABS = [
  { key: "report", label: "财报公告", category: "report" },
  { key: "event", label: "重大事项", category: "event" },
] as const;

const PER_TAB = 10;
const FETCH_LIMIT = 60;

/**
 * 首页快讯区：公告流两页（财报公告 / 重大事项），零新数据源。
 *
 * 复用 `dataFace/AnnouncementFeed`（巨潮资讯网公告，经 `/market/announcements`），
 * 两页共享一次请求、客户端按 category 过滤，各取 10 条时间倒序；MVP 不做无限滚动，
 * 底部「查看全部」跳 /market。无新闻源（TuShare 新闻接口积分门槛）故不造伪新闻流。
 */
export function MarketNewsFeed() {
  return (
    <div className="market-news-feed">
      <Tabs
        defaultActiveKey="report"
        destroyInactiveTabPane
        items={TABS.map((t) => ({
          key: t.key,
          label: t.label,
          children: (
            <AnnouncementFeed
              category={t.category}
              fetchLimit={FETCH_LIMIT}
              maxRows={PER_TAB}
              timeMode="relative"
            />
          ),
        }))}
      />
      <div className="market-news-feed__foot">
        <Link to="/market">查看全部 ›</Link>
      </div>
    </div>
  );
}
