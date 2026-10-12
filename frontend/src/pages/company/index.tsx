import { PlaceholderPage } from "@/shared/ui/PlaceholderPage";

/** 一级导航「公司分析」的占位页（IA 文档 §5.2 no-404 契约）——模块真页面落地时替换本文件，路由形状不变 */
export default function CompanyPage() {
  return <PlaceholderPage title="公司分析" hint="9 张公司卡片尚未落地；本页为占位路由，原 /stock/:symbol 已 redirect 到此" />;
}
