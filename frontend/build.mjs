import { copyFile, mkdir, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import path from "node:path";

const frontendDir = path.dirname(fileURLToPath(import.meta.url));
const repositoryDir = path.resolve(frontendDir, "..");
const outputDir = path.join(frontendDir, "dist");
const configuredApi = (process.env.VITE_API_BASE_URL || "").trim().replace(/\/+$/, "");

if (!configuredApi) {
  throw new Error("Set VITE_API_BASE_URL to the HTTPS address of the FastAPI backend before building the hosted frontend.");
}

const parsedApi = new URL(configuredApi);
if (parsedApi.protocol !== "https:" && !["localhost", "127.0.0.1"].includes(parsedApi.hostname)) {
  throw new Error("VITE_API_BASE_URL must use HTTPS outside localhost.");
}

await mkdir(outputDir, { recursive: true });
await copyFile(path.join(repositoryDir, "app", "static", "review.html"), path.join(outputDir, "index.html"));
await writeFile(
  path.join(outputDir, "api-config.js"),
  `window.CCTV_API_BASE_URL = ${JSON.stringify(configuredApi)};\n`,
  "utf8",
);
console.log(`Built review interface for ${parsedApi.origin}`);
