import { useParams } from "react-router-dom";
import { TagsPanel } from "@/features/watchlist/components";

/**
 * `/tags/:tagName`：**保留路由（语义未变）**——带参访问仍是可达入口，
 * redirect 只是把 `/me?tab=tags&name=:tagName` 作为新的规范位置。
 * 内容复用 `TagsPanel`（与 `/me?tab=tags&name=:tagName` **同一实现**，不许两份）。
 */
export default function TagsDetailPage() {
  const { tagName = "" } = useParams();
  return <TagsPanel tagName={tagName} />;
}
