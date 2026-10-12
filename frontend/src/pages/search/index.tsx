import { PlaceholderPage } from "@/shared/ui";

/** 一级导航「搜索」的占位页（IA 文档 §5.2 no-404 契约）——模块真页面落地时替换本文件，路由形状不变 */
export default function SearchPage() {
  return <PlaceholderPage title="搜索" hint="顶栏全局搜索的目标页（跨股票 / 行业 / 指数 / 概念 / 资讯），可见性待定；本任务只保证 ?q= 可达、不 404" highlightQueryKeys={["q"]} />;
}
