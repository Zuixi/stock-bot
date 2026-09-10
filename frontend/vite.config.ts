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
    port: 3000,
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
      // auth-service（登录/会话/JWKS）与业务 API 同源走 Vite 代理，
      // 保证 Cookie 域一致与 CSRF double-submit 正常工作
      "/auth": {
        target: "http://localhost:8001",
        changeOrigin: true,
      },
      "/.well-known": {
        target: "http://localhost:8001",
        changeOrigin: true,
      },
    },
  },
});
