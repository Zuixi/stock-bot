// 必须是本模块的第一条 import：它在模块求值期就注册 `vite:preloadError` 监听，
// 早于 React 挂载、早于任何懒加载路由分块被请求（详见该文件头部的定因说明）。
// import 语句本身会被提升，位置只是为了让"先注册"这件事在代码里一眼可见。
import "@/app/chunk-error-recovery";
import React from "react";
import ReactDOM from "react-dom/client";
import "@/app/styles/theme.css";
import App from "./App";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
