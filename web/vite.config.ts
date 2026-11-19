import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// In dev, proxy API calls to NGINX so requests still go through the load balancer.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: { "/api": "http://127.0.0.1:8088", "/health": "http://127.0.0.1:8088" },
  },
});
