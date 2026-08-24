import { defineConfig } from "vite";
import { sveltekit } from "@sveltejs/kit/vite";

// @ts-expect-error process is a nodejs global
const host = process.env.TAURI_DEV_HOST;
// @ts-expect-error process is a nodejs global
const apiPort = process.env.PCBRIDGE_HTTP_PORT || "8001";

export default defineConfig(async () => ({
  plugins: [sveltekit()],

  clearScreen: false,

  server: {
    port: 1420,
    strictPort: true,
    host: host || false,
    hmr: host ? { protocol: "ws", host, port: 1421 } : undefined,
    watch: {
      ignored: ["**/src-tauri/**"],
    },

    // Keeps the API same-origin during development, exactly as it is for
    // a phone in production. Without this the browser would treat every
    // API call as cross-origin and the event stream would need its own
    // special-case URL — one more way dev and production could diverge.
    proxy: {
      "/api": {
        target: `http://127.0.0.1:${apiPort}`,
        changeOrigin: true,
      },
      "/events": {
        target: `http://127.0.0.1:${apiPort}`,
        changeOrigin: true,
        // Server-Sent Events must not be buffered or the interface
        // would only update in bursts when a chunk happened to flush.
        configure: (/** @type {any} */ proxy) => {
          proxy.on("proxyRes", (/** @type {any} */ proxyRes) => {
            proxyRes.headers["cache-control"] = "no-cache, no-transform";
          });
        },
      },
    },
  },
}));
