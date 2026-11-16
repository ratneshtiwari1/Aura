import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';

const DEFAULT_DEV_PORT = 5173;
const DEFAULT_API_TARGET = 'http://127.0.0.1:8000';
function devPort(value: string | undefined): number {
  const parsed = Number(value);
  return Number.isInteger(parsed) && parsed > 0 && parsed <= 65_535
    ? parsed
    : DEFAULT_DEV_PORT;
}

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '');

  return {
    plugins: [react()],
    server: {
      port: devPort(env.VITE_DEV_PORT),
      proxy: {
        '/api': {
          target: env.VITE_API_PROXY_TARGET || DEFAULT_API_TARGET,
          changeOrigin: true,
        },
      },
    },
  };
});
