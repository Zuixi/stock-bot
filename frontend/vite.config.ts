import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "path";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "src"),
    },
  },
  server: {
    host: "127.0.0.1", // Windows 下 vite 默认可能只绑 [::1]，Chromium/Playwright 走 IPv4 会拒连
    port: 3000,
    proxy: {
      // 统一经由 Gateway(:80) 反代——P7 端口收敛后 api/auth-service 无宿主机端口，
      // 直连 8000/8001 已不可达；走网关同时保住 Cookie 同源与 CSRF 链路
      "/api": {
        target: "http://localhost:80",
        changeOrigin: true,
      },
      "/auth": {
        target: "http://localhost:80",
        changeOrigin: true,
      },
      "/.well-known": {
        target: "http://localhost:80",
        changeOrigin: true,
      },
    },
  },
});
