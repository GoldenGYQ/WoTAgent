import { defineConfig } from 'vitest/config'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    host: '0.0.0.0',
    // 透传/反代场景下，Host header 可能是随机域名（例如 localtunnel / cloudflared）。
    // 这里放开 Host 校验，避免 Vite 返回 "Blocked request"。
    // 注意：仅建议用于本地开发调试环境。
    allowedHosts: true,
    // 如果你本机端口冲突，让 Vite 自动找可用端口即可（启动日志里会打印最终端口）
    port: 5174,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
      '/ws/v2': {
        target: 'ws://127.0.0.1:8000',
        ws: true,
      },
    },
  },
  test: {
    environment: 'happy-dom',
    globals: true,
  },
})
