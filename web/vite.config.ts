import path from "node:path"
import { defineConfig } from "vite"
import react from "@vitejs/plugin-react"
import tailwindcss from "@tailwindcss/vite"

// https://vite.dev/config/
// 管理面板挂载在 /panel（官网已拆分至独立仓库 xingchao_site，挂载在 /）
export default defineConfig({
  plugins: [react(), tailwindcss()],
  base: "/panel/",
  resolve: {
    alias: { "@": path.resolve(__dirname, "./src") },
  },
})
