import { mkdirSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
export const projectRoot = fileURLToPath(new URL("../", import.meta.url));
export function configureRuntime() {
  const runtime = path.join(projectRoot, ".runtime");
  for (const [key, value] of Object.entries({
    CLOUDFLARE_CF_FETCH_ENABLED: "false",
    WRANGLER_SEND_METRICS: "false",
    WRANGLER_WRITE_LOGS: "false",
    WRANGLER_LOG_PATH: path.join(runtime, "logs"),
    WRANGLER_REGISTRY_PATH: path.join(runtime, "registry"),
    MINIFLARE_REGISTRY_PATH: path.join(runtime, "miniflare"),
  }))
    process.env[key] ??= value;
  for (const key of [
    "WRANGLER_LOG_PATH",
    "WRANGLER_REGISTRY_PATH",
    "MINIFLARE_REGISTRY_PATH",
  ])
    mkdirSync(process.env[key], { recursive: true });
}
configureRuntime();
process.chdir(projectRoot);
