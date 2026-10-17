// Optional local runtime snapshot. Canonical generation: python -m cashguard.contracts.export.
import { writeFile } from "node:fs/promises";

const origin = process.env.CG_API_ORIGIN || "http://127.0.0.1:8127";
const url = new URL(origin);
if (!["127.0.0.1", "localhost", "[::1]"].includes(url.hostname))
  throw new Error("Use a local backend export");
const response = await fetch(new URL("/openapi.json", url));
if (!response.ok) throw new Error(`OpenAPI: ${response.status}`);
const schema = await response.json();
if (!schema.paths?.["/api/ui/jobs/{job_id}"]) throw new Error("Missing UI API");
await writeFile(
  new URL("src/shared/api/openapi.json", import.meta.url),
  `${JSON.stringify(schema, null, 2)}\n`,
);
console.log("Exported backend OpenAPI. Run pnpm gen:api.");
