import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';
var DEFAULT_DEV_PORT = 5173;
var DEFAULT_API_TARGET = 'http://127.0.0.1:8000';
function devPort(value) {
    var parsed = Number(value);
    return Number.isInteger(parsed) && parsed > 0 && parsed <= 65535
        ? parsed
        : DEFAULT_DEV_PORT;
}
export default defineConfig(function (_a) {
    var mode = _a.mode;
    var env = loadEnv(mode, process.cwd(), '');
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
