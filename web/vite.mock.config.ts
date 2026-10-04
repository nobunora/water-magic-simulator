import { mergeConfig } from "vite";
import baseConfig from "./vite.config";

export default mergeConfig(baseConfig, {
  server: {
    host: "127.0.0.1",
    port: 5175,
    strictPort: true,
    fs: { allow: [".."] },
    proxy: { "/api": "http://127.0.0.1:8014" },
  },
});
