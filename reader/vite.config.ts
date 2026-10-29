import vinext from "vinext";
import { defineConfig } from "vite";
import { configureRuntime } from "./scripts/runtime-env.mjs";
configureRuntime();
export default defineConfig(async () => {
  const { cloudflare } = await import("@cloudflare/vite-plugin");
  return {
    plugins: [
      vinext(),
      cloudflare({
        viteEnvironment: { name: "rsc", childEnvironments: ["ssr"] },
        inspectorPort: false,
      }),
    ],
  };
});
